"""The approval gate. This module is the only place in edge/ that:

  - constructs a StackQL MCP server in full_access mode
  - names run_mutation_query as a tool to call

and it never hands that server to a model. Two kinds of statement reach it:

  - proposed by the decision agent (the rate limit REPLACE it discovered the contract for and put
    in its structured output), or
  - code-owned, rendered from a query file (edge/restore_rate_limit for the restore subcommand,
    edge/decision_issue for the optional GitHub decision record).

Both go through the same allowlist before an operator sees them. Sequence, each step required
for the next:

  1. prepare_proposed() / prepare_code() check the statement: exactly one statement, the verb and
     provider.service.resource pair on the allowlist for the action, no other verb or comment, the
     WHERE clause pinned to the demo zone and the rate limit phase (or the decision repo), the
     rules JSON carrying DEMO_PREFIX in its description and a requests_per_period equal to the
     policy threshold - and the threshold itself one of the policy's two values. Anything else is
     rejected before approval is even asked for.
  2. An Approval is minted only by the terminal prompt (`approve <proposal-id>`) or the explicit
     --approve flag, and carries a nonce created for this proposal. --decline records the abort.
  3. execute_approved() re-checks the nonce, opens the full_access server, asserts the target with
     the code-owned SELECT on that same server (the rule description carries DEMO_PREFIX; the
     decision repo accepts issues), sends exactly one run_mutation_query, then verifies with the
     code-owned SELECT and, when the model proposed one, runs its verification SELECT too after
     validate_select_query.

tests/test_gate.py proves the read-only servers never expose the mutation tools, that the allowlist
rejects everything but the one shape, and that execute_approved refuses to run without a matching
approval."""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from rich.markup import escape

from .config import settings
from .costs import console
from .mcp import StackQLServer, stackql_server, tool_is_error, tool_rows, tool_text
from .queries import render_query

EXECUTOR_TOOLS = ("run_select_query", "validate_select_query", "run_mutation_query")

# action -> (verb, provider.service.resource). The only writes this use case can ever send.
ALLOWLIST: dict[str, tuple[str, str]] = {
    "set_rate_limit_threshold": ("REPLACE", "cloudflare.rulesets.phases"),
    "file_decision_issue": ("INSERT", "github.issues.issues"),
}
CODE_OWNED: dict[str, str] = {
    "set_rate_limit_threshold": "edge/restore_rate_limit",
    "file_decision_issue": "edge/decision_issue",
}
RATE_LIMIT_PHASE = "http_ratelimit"
ASSERTION_QUERY = "edge/assert_demo_rule"

_VERBS = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "REPLACE",
    "EXEC",
    "DROP",
    "CREATE",
    "ALTER",
    "TRUNCATE",
    "MERGE",
    "UPSERT",
    "GRANT",
    "REVOKE",
    "PRAGMA",
    "ATTACH",
    "SHOW",
    "DESCRIBE",
)
_VERB_RE = re.compile(r"\b(" + "|".join(_VERBS) + r")\b", re.IGNORECASE)
_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")
_WHERE_TERM_RE = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*'((?:[^']|'')*)'\s*$")

Assertion = Callable[[StackQLServer], Awaitable[None]]


class ApprovalDenied(RuntimeError):
    pass


class StatementRejected(ApprovalDenied):
    """The statement is not on the allowlist or not pinned to the demo target."""


class TargetAssertionFailed(RuntimeError):
    pass


class VerificationFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class PendingMutation:
    proposal_id: str
    action: str
    source: str  # "model" | "edge/<query id>"
    sql: str
    nonce: str
    verify_sql: str = ""  # a model-proposed verification SELECT, checked and validated before use
    params: dict[str, str] | None = None


@dataclass(frozen=True)
class Approval:
    proposal_id: str
    nonce: str
    approver: str
    method: str  # "terminal" | "flag"


# --- the allowlist ------------------------------------------------------------------------------


def _skeleton(sql: str) -> str:
    """The statement with every single-quoted literal blanked, so keywords inside JSON or text
    values do not count and separators inside literals do not split."""
    return _LITERAL_RE.sub("''", sql)


def _single_statement(sql: str) -> str:
    text = (sql or "").strip()
    if not text:
        raise StatementRejected("empty statement")
    if text.endswith(";"):
        text = text[:-1].rstrip()
    skel = _skeleton(text)
    if ";" in skel:
        raise StatementRejected("more than one statement (a ';' outside a literal)")
    if "--" in skel or "/*" in skel:
        raise StatementRejected("comments are not accepted in a statement")
    if _LITERAL_RE.sub("", text).count("'") % 2:
        raise StatementRejected("unbalanced quote")
    return text


def _verbs_in(sql: str) -> list[str]:
    return [v.upper() for v in _VERB_RE.findall(_skeleton(sql))]


def _pins(where: str) -> dict[str, str]:
    """`a = 'x' AND b = 'y'` -> {a: x, b: y}; anything else in the WHERE clause is rejected."""
    pins: dict[str, str] = {}
    for term in re.split(r"\bAND\b", where, flags=re.IGNORECASE):
        m = _WHERE_TERM_RE.match(term)
        if not m:
            raise StatementRejected(f"WHERE term {term.strip()!r} is not `column = 'literal'`")
        pins[m.group(1).lower()] = m.group(2).replace("''", "'")
    return pins


def _expect_pins(pins: dict[str, str], expected: dict[str, str]) -> None:
    if pins != expected:
        want = " AND ".join(f"{k} = '{v}'" for k, v in expected.items())
        raise StatementRejected(f"WHERE must be exactly `{want}`, got {pins}")


def _rate_limit_pins() -> dict[str, str]:
    s = settings()
    if not s.cloudflare_zone_id:
        raise StatementRejected("CLOUDFLARE_ZONE_ID is not set; cannot pin the target")
    return {"zone_id": s.cloudflare_zone_id, "ruleset_phase": RATE_LIMIT_PHASE}


def _check_rules_json(literal: str, threshold: int) -> None:
    s = settings()
    try:
        rules = json.loads(literal.replace("''", "'"))
    except json.JSONDecodeError as e:
        raise StatementRejected(f"rules is not valid JSON: {e}") from e
    if not isinstance(rules, list) or len(rules) != 1 or not isinstance(rules[0], dict):
        raise StatementRejected("rules must be a JSON array with exactly the one demo rule")
    rule = rules[0]
    desc = str(rule.get("description") or "")
    if s.demo_prefix not in desc:
        raise StatementRejected(
            f"rule description {desc!r} does not contain DEMO_PREFIX {s.demo_prefix!r}"
        )
    got = (rule.get("ratelimit") or {}).get("requests_per_period")
    if not isinstance(got, int) or isinstance(got, bool) or got != int(threshold):
        raise StatementRejected(
            f"rules.ratelimit.requests_per_period is {got!r}, the policy threshold is {threshold}"
        )


def check_statement(
    action: str,
    sql: str,
    *,
    threshold: int | None = None,
    owner: str | None = None,
    repo: str | None = None,
) -> str:
    """Return the normalised statement or raise StatementRejected. Pure code, no server."""
    if action not in ALLOWLIST:
        raise StatementRejected(f"action {action!r} is not on the mutation allowlist")
    verb, resource = ALLOWLIST[action]
    text = _single_statement(sql)
    verbs = _verbs_in(text)
    if not verbs or verbs[0] != verb:
        raise StatementRejected(f"statement must start with {verb}, got {verbs[:1] or 'nothing'}")

    if action == "set_rate_limit_threshold":
        if verbs != [verb]:
            raise StatementRejected(f"only one verb allowed, found {verbs}")
        if threshold is None:
            raise StatementRejected("set_rate_limit_threshold needs the policy threshold")
        allowed = settings().allowed_thresholds
        if int(threshold) not in allowed:
            raise StatementRejected(
                f"threshold {threshold} is outside the policy values {sorted(allowed)} "
                "(TIGHTENED_THRESHOLD / BASELINE_THRESHOLD)"
            )
        pattern = re.compile(
            rf"^{verb}\s+{re.escape(resource)}\s+SET\s+rules\s*=\s*'((?:[^']|'')*)'\s+WHERE\s+(.+)$",
            re.IGNORECASE | re.DOTALL,
        )
        m = pattern.match(text)
        if not m:
            raise StatementRejected(
                f"statement is not `{verb} {resource} SET rules = '<json>' WHERE ...`"
            )
        _check_rules_json(m.group(1), int(threshold))
        _expect_pins(_pins(m.group(2)), _rate_limit_pins())
        return text

    # file_decision_issue: the code-owned issue statement, pinned to the decision repo
    if verbs != [verb, "SELECT"]:
        raise StatementRejected(
            f"expected `{verb} ... SELECT ...` with no other verb, found {verbs}"
        )
    pattern = re.compile(
        rf"^{verb}\s+INTO\s+{re.escape(resource)}\s*\(([^)]*)\)\s+SELECT\s+(.+)$",
        re.IGNORECASE | re.DOTALL,
    )
    m = pattern.match(text)
    if not m:
        raise StatementRejected(f"statement is not `{verb} INTO {resource} (...) SELECT ...`")
    columns = [c.strip().lower() for c in m.group(1).split(",")]
    literals = [lit[1:-1].replace("''", "'") for lit in _LITERAL_RE.findall(m.group(2))]
    if not owner or not repo:
        raise StatementRejected("file_decision_issue needs the decision repo owner and name")
    if columns[:2] != ["owner", "repo"] or literals[:2] != [owner, repo]:
        raise StatementRejected(
            f"issue must be pinned to owner '{owner}', repo '{repo}' as the first two values"
        )
    return text


def check_verification_select(sql: str) -> str:
    """A model-proposed verification SELECT: one flat SELECT on the rate limit phase resource,
    pinned to the demo zone and phase. It is also validated on the executor before it runs."""
    _, resource = ALLOWLIST["set_rate_limit_threshold"]
    text = _single_statement(sql)
    if _verbs_in(text) != ["SELECT"]:
        raise StatementRejected("verification must be a single flat SELECT")
    m = re.match(
        rf"^SELECT\s+.+?\s+FROM\s+{re.escape(resource)}\s+WHERE\s+(.+)$",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        raise StatementRejected(f"verification must read {resource} with a WHERE clause")
    _expect_pins(_pins(m.group(1)), _rate_limit_pins())
    return text


# --- preparing a proposal -------------------------------------------------------------------------


def prepare_proposed(
    action: str,
    sql: str,
    proposal_id: str,
    *,
    threshold: int | None = None,
    verify_sql: str = "",
) -> PendingMutation:
    """A statement the decision agent discovered and proposed. Checked before anyone sees it."""
    if action != "set_rate_limit_threshold":
        raise StatementRejected(
            f"the model may only propose set_rate_limit_threshold, not {action!r}"
        )
    if not (sql or "").strip():
        raise StatementRejected("the decision is tighten but no statement was proposed")
    text = check_statement(action, sql, threshold=threshold)
    verify = check_verification_select(verify_sql) if (verify_sql or "").strip() else ""
    return PendingMutation(
        proposal_id=proposal_id,
        action=action,
        source="model",
        sql=text,
        nonce=secrets.token_hex(8),
        verify_sql=verify,
        params={"threshold": str(threshold)},
    )


def prepare_code(action: str, params: dict[str, Any], proposal_id: str) -> PendingMutation:
    """A code-owned statement rendered from its query file, checked like any other."""
    if action not in CODE_OWNED:
        raise StatementRejected(f"action {action!r} has no code-owned statement")
    qid = CODE_OWNED[action]
    sql = render_query(qid, **params)
    if action == "set_rate_limit_threshold":
        try:
            threshold: int | None = int(params["threshold"])
        except (KeyError, TypeError, ValueError) as e:
            raise StatementRejected("set_rate_limit_threshold needs an integer threshold") from e
        text = check_statement(action, sql, threshold=threshold)
    else:
        text = check_statement(
            action, sql, owner=str(params.get("github_owner")), repo=str(params.get("github_repo"))
        )
    return PendingMutation(
        proposal_id=proposal_id,
        action=action,
        source=qid,
        sql=text,
        nonce=secrets.token_hex(8),
        params={k: str(v) for k, v in params.items()},
    )


def show(pending: PendingMutation) -> None:
    console.rule("[bold red]approval gate[/bold red]")
    origin = (
        "proposed by the decision agent, checked against the allowlist"
        if pending.source == "model"
        else f"code-owned (edge/queries/{pending.source.split('/')[-1]}.sql), checked against the allowlist"
    )
    console.print(f"proposal {pending.proposal_id}: {pending.action} - {origin}")
    console.print(f"[bold]{escape(pending.sql)}[/bold]")
    if pending.verify_sql:
        console.print(f"verification proposed: [cyan]{escape(pending.verify_sql)}[/cyan]")


def ask_terminal(pending: PendingMutation, approver: str = "operator") -> Approval | None:
    """The explicit human step. An Approval exists only if the operator types the exact phrase."""
    show(pending)
    phrase = f"approve {pending.proposal_id}"
    console.print(f"type [bold]{phrase}[/bold] to execute, anything else to decline: ", end="")
    try:
        answer = input().strip()
    except EOFError:
        answer = ""
    if answer != phrase:
        console.print("[yellow]declined - no mutation will run[/yellow]")
        return None
    return Approval(pending.proposal_id, pending.nonce, approver, "terminal")


def flag_approval(pending: PendingMutation) -> Approval:
    """For --approve (unattended runs): the flag is a documented, explicit operator decision."""
    show(pending)
    console.print("[yellow]--approve given: approval recorded from the flag[/yellow]")
    return Approval(pending.proposal_id, pending.nonce, "operator (--approve)", "flag")


def declined(pending: PendingMutation) -> None:
    """For --decline: show the statement, record that it was not run."""
    show(pending)
    console.print("[yellow]--decline given: declined, no mutation will run[/yellow]")


# --- target assertions and verifications: code-owned SELECTs on the executor server ------------


async def read_rate_limit_rule(server: StackQLServer) -> dict[str, Any]:
    res = await server.call_tool(
        "run_select_query", {"sql": render_query(ASSERTION_QUERY), "format": "json"}
    )
    if tool_is_error(res):
        raise TargetAssertionFailed(f"ruleset read failed: {tool_text(res)[:300]}")
    rows = tool_rows(res)
    return rows[0] if rows else {}


def assert_demo_rule() -> Assertion:
    """The first rule in the http_ratelimit phase must carry DEMO_PREFIX in its description."""

    async def _check(server: StackQLServer) -> None:
        s = settings()
        row = await read_rate_limit_rule(server)
        desc = str(row.get("description") or "")
        if s.demo_prefix not in desc:
            raise TargetAssertionFailed(
                f"rate limit rule description {desc!r} does not contain {s.demo_prefix!r}: "
                "this rule was not created by edge/stack, refusing to mutate"
            )
        console.print(f"target assertion ok: rule {row.get('rule_id')} description {desc!r}")

    return _check


def verify_threshold(expected: int) -> Assertion:
    async def _check(server: StackQLServer) -> None:
        row = await read_rate_limit_rule(server)
        got = row.get("threshold")
        try:
            ok = int(float(got)) == int(expected)
        except (TypeError, ValueError):
            ok = False
        if not ok:
            raise VerificationFailed(f"threshold after mutation is {got!r}, expected {expected}")
        console.print(f"verified (code-owned SELECT): requests_per_period = {expected}")

    return _check


async def run_proposed_verification(server: StackQLServer, sql: str) -> list[dict]:
    """validate_select_query, then run the SELECT the model proposed; rows are printed for the
    operator, the code-owned verification remains the one that decides."""
    val = await server.call_tool("validate_select_query", {"sql": sql, "format": "json"})
    body = tool_text(val)
    if tool_is_error(val) or '"valid":false' in body.replace(" ", ""):
        console.print(
            f"[yellow]proposed verification did not validate:[/yellow] {escape(body[:200])}"
        )
        return []
    res = await server.call_tool("run_select_query", {"sql": sql, "format": "json"})
    rows = tool_rows(res) if not tool_is_error(res) else []
    console.print(f"proposed verification rows: {escape(json.dumps(rows)[:300])}")
    return rows


def assert_decisions_repo(owner: str, repo: str) -> Assertion:
    """The configured GITHUB_DECISIONS_REPO is reachable with the token and accepts issues."""

    async def _check(server: StackQLServer) -> None:
        res = await server.call_tool(
            "run_select_query",
            {
                "sql": render_query("edge/decision_repo", github_owner=owner, github_repo=repo),
                "format": "json",
            },
        )
        rows = tool_rows(res)
        if tool_is_error(res) or not rows:
            raise TargetAssertionFailed(f"{owner}/{repo}: not reachable: {tool_text(res)[:300]}")
        row = rows[0]
        if str(row.get("has_issues")).lower() not in ("true", "1"):
            raise TargetAssertionFailed(f"{owner}/{repo} has issues disabled")
        if str(row.get("archived")).lower() in ("true", "1"):
            raise TargetAssertionFailed(f"{owner}/{repo} is archived")
        console.print(f"target assertion ok: {row.get('full_name')} accepts issues")

    return _check


async def execute_approved(
    pending: PendingMutation,
    approval: Approval | None,
    *,
    assert_target: Assertion,
    verify: Assertion | None = None,
) -> dict[str, Any]:
    """Execute exactly one approved statement after asserting the target on the same server."""
    if approval is None:
        raise ApprovalDenied("no approval")
    if approval.proposal_id != pending.proposal_id or approval.nonce != pending.nonce:
        raise ApprovalDenied("approval does not match this proposal")
    server = stackql_server(
        name="stackql-executor", mode="full_access", allowed_tools=EXECUTOR_TOOLS
    )
    proposed_rows: list[dict] = []
    async with server:
        await assert_target(server)
        res = await server.call_tool("run_mutation_query", {"sql": pending.sql})
        out = tool_text(res).strip()
        if tool_is_error(res):
            raise RuntimeError(f"mutation refused or failed: {out[:400]}")
        console.print(f"[green]executed via run_mutation_query[/green]: {escape(out[:200])}")
        if verify is not None:
            await verify(server)
        if pending.verify_sql:
            proposed_rows = await run_proposed_verification(server, pending.verify_sql)
    return {
        "proposal_id": pending.proposal_id,
        "action": pending.action,
        "source": pending.source,
        "sql": pending.sql,
        "verify_sql": pending.verify_sql or None,
        "proposed_verification_rows": proposed_rows,
        "approved_by": approval.approver,
        "method": approval.method,
        "server_response": out[:400],
    }
