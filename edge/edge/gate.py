"""The approval gate. This module is the only place in edge/ that:

  - constructs a StackQL MCP server in full_access mode
  - names run_mutation_query as a tool to call

and it never hands that server to a model. The approved statement is rendered from a query file
(edge/tighten_rate_limit or edge/decision_issue) and sent by code, so the change is one logged SQL
statement in the server audit file, like every SELECT before it.

Sequence, each step required for the next:

  1. prepare() renders the one statement an action maps to. Unknown actions and thresholds
     outside the policy's two values cannot be prepared.
  2. An Approval is minted only by the terminal prompt (`approve <proposal-id>`) or the explicit
     --approve flag, and carries a nonce created for this proposal. --decline records the abort.
  3. execute_approved() re-checks the nonce, opens the full_access server, asserts the target with
     a SELECT on that same server (the rule description carries DEMO_PREFIX; the decision repo
     accepts issues), sends exactly one run_mutation_query, then verifies with another SELECT.

tests/test_gate.py proves the read-only servers never expose the mutation tools and that
execute_approved refuses to run without a matching approval."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from rich.markup import escape

from .config import settings
from .costs import console
from .mcp import StackQLServer, stackql_server, tool_is_error, tool_rows, tool_text
from .queries import render_query

EXECUTOR_TOOLS = ("run_select_query", "run_mutation_query")
ALLOWED_MUTATIONS = {
    "set_rate_limit_threshold": "edge/tighten_rate_limit",
    "file_decision_issue": "edge/decision_issue",
}

Assertion = Callable[[StackQLServer], Awaitable[None]]


class ApprovalDenied(RuntimeError):
    pass


class TargetAssertionFailed(RuntimeError):
    pass


class VerificationFailed(RuntimeError):
    pass


@dataclass(frozen=True)
class PendingMutation:
    proposal_id: str
    action: str
    query_id: str
    params: dict[str, str]
    sql: str
    nonce: str


@dataclass(frozen=True)
class Approval:
    proposal_id: str
    nonce: str
    approver: str
    method: str  # "terminal" | "flag"


def prepare(action: str, params: dict[str, Any], proposal_id: str) -> PendingMutation:
    """Render the one statement this action maps to. Anything off the menu cannot be prepared."""
    if action not in ALLOWED_MUTATIONS:
        raise ApprovalDenied(f"action {action!r} is not on the mutation menu")
    if action == "set_rate_limit_threshold":
        allowed = settings().allowed_thresholds
        try:
            threshold = int(params["threshold"])
        except (KeyError, TypeError, ValueError) as e:
            raise ApprovalDenied("set_rate_limit_threshold needs an integer threshold") from e
        if threshold not in allowed:
            raise ApprovalDenied(
                f"threshold {threshold} is outside the policy values {sorted(allowed)} "
                "(TIGHTENED_THRESHOLD / BASELINE_THRESHOLD)"
            )
    qid = ALLOWED_MUTATIONS[action]
    return PendingMutation(
        proposal_id=proposal_id,
        action=action,
        query_id=qid,
        params={k: str(v) for k, v in params.items()},
        sql=render_query(qid, **params),
        nonce=secrets.token_hex(8),
    )


def show(pending: PendingMutation) -> None:
    console.rule("[bold red]approval gate[/bold red]")
    console.print(f"proposal {pending.proposal_id}: {pending.action}")
    console.print(f"statement (edge/queries/{pending.query_id.split('/')[-1]}.sql), rendered:")
    console.print(f"[bold]{escape(pending.sql)}[/bold]")


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


# --- target assertions and verifications: SELECTs on the executor server -----------------------


async def read_rate_limit_rule(server: StackQLServer) -> dict[str, Any]:
    res = await server.call_tool(
        "run_select_query", {"sql": render_query("edge/rate_limit_ruleset"), "format": "json"}
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
        console.print(f"verified: requests_per_period = {expected}")

    return _check


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
    async with server:
        await assert_target(server)
        res = await server.call_tool("run_mutation_query", {"sql": pending.sql})
        out = tool_text(res).strip()
        if tool_is_error(res):
            raise RuntimeError(f"mutation refused or failed: {out[:400]}")
        console.print(f"[green]executed via run_mutation_query[/green]: {escape(out[:200])}")
        if verify is not None:
            await verify(server)
    return {
        "proposal_id": pending.proposal_id,
        "action": pending.action,
        "query_id": pending.query_id,
        "sql": pending.sql,
        "approved_by": approval.approver,
        "method": approval.method,
        "server_response": out[:400],
    }
