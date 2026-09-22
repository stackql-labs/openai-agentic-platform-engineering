"""The approval gate. This module is the only place in the repository that:

  - constructs a StackQL MCP server in full_access mode
  - exposes run_mutation_query / run_lifecycle_operation to anything

and it never hands those tools to a model. The approved statement is rendered from the query
library (triage/aws_asg_scale_out) and executed by code through the MCP server, so the mutation is
a single logged SQL statement in the server audit file, exactly like every SELECT before it.

Sequence, all of which must succeed in order:

  1. An Approval is minted only by the terminal prompt (or the explicit --approve rehearsal flag)
     and carries a nonce created for this proposal.
  2. execute_approved() re-checks the nonce, then asserts the demo tag on the target with a SELECT
     (triage/aws_asg_tags) on the same server. No tag, no mutation.
  3. The statement is sent to run_mutation_query. Anything else raises before that call.

tests/test_gate.py proves the read-only servers never expose the mutation tools and that
execute_approved refuses to run without a matching approval.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass

from oape_agents.common.config import settings
from oape_agents.common.costs import console
from oape_agents.common.mcp import stackql_mcp_server
from oape_agents.common.queries import load_query

EXECUTOR_TOOLS = ("run_select_query", "run_mutation_query", "run_lifecycle_operation")
ALLOWED_MUTATIONS = {"scale_out_by_one": "triage/aws_asg_scale_out"}


class ApprovalDenied(RuntimeError):
    pass


class TagAssertionFailed(RuntimeError):
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
    method: str  # "terminal" | "rehearsal-flag"


def prepare(action: str, params: dict[str, str], proposal_id: str) -> PendingMutation:
    """Render the one statement this action maps to. Unknown actions cannot be prepared."""
    if action not in ALLOWED_MUTATIONS:
        raise ApprovalDenied(f"action {action!r} is not on the mutation menu")
    qid = ALLOWED_MUTATIONS[action]
    sql = load_query(qid).render(**params)
    return PendingMutation(
        proposal_id=proposal_id,
        action=action,
        query_id=qid,
        params=dict(params),
        sql=sql,
        nonce=secrets.token_hex(8),
    )


def ask_terminal(pending: PendingMutation, approver: str = "operator") -> Approval | None:
    """The explicit human step. Returns an Approval only when the operator types the exact phrase."""
    console.rule("[bold red]approval gate[/bold red]")
    console.print(f"proposal {pending.proposal_id}: {pending.action}")
    console.print(f"statement (queries/{pending.query_id}.sql):")
    console.print(f"[bold]{pending.sql}[/bold]")
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


def rehearsal_approval(pending: PendingMutation) -> Approval:
    """For `--approve` rehearsals only: the flag is a documented, explicit operator decision."""
    console.print("[yellow]--approve given: rehearsal approval recorded[/yellow]")
    return Approval(pending.proposal_id, pending.nonce, "operator (--approve)", "rehearsal-flag")


def _tool_text(result) -> str:
    return "\n".join(getattr(c, "text", "") for c in (result.content or []))


async def execute_approved(pending: PendingMutation, approval: Approval | None) -> dict:
    """Execute exactly one approved statement, after asserting the demo tag on the target."""
    if approval is None:
        raise ApprovalDenied("no approval")
    if approval.proposal_id != pending.proposal_id or approval.nonce != pending.nonce:
        raise ApprovalDenied("approval does not match this proposal")
    s = settings()
    server = stackql_mcp_server(
        name="stackql-executor", mode="full_access", allowed_tools=EXECUTOR_TOOLS
    )
    async with server:
        # 1. tag assertion through the same server, as a SELECT
        tag_sql = load_query("triage/aws_asg_tags").render()
        res = await server.call_tool("run_select_query", {"sql": tag_sql, "format": "json"})
        text = _tool_text(res)
        tagged = False
        try:
            rows = json.loads(text).get("rows", []) if text.strip().startswith("{") else []
        except json.JSONDecodeError:
            rows = []
        for r in rows:
            if (
                str(r.get("tag_key")) == s.demo_tag_key
                and str(r.get("tag_value")) == s.demo_tag_value
            ):
                tagged = True
        if not tagged:
            raise TagAssertionFailed(
                f"target does not carry {s.demo_tag_key}={s.demo_tag_value}: refusing to mutate"
            )
        console.print(
            f"tag assertion ok: {s.demo_tag_key}={s.demo_tag_value} on {pending.params.get('checkout_asg_name', 'target')}"
        )
        # 2. the one mutation
        res = await server.call_tool("run_mutation_query", {"sql": pending.sql})
        out = _tool_text(res)
        if getattr(res, "is_error", False) or getattr(res, "isError", False):
            raise RuntimeError(f"mutation refused or failed: {out[:400]}")
    console.print(f"[green]executed via run_mutation_query[/green]: {out.strip()[:200]}")
    return {
        "proposal_id": pending.proposal_id,
        "query_id": pending.query_id,
        "sql": pending.sql,
        "approved_by": approval.approver,
        "method": approval.method,
        "server_response": out.strip()[:400],
    }
