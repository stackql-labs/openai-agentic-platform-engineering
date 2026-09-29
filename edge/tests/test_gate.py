"""The mutation path is provably unreachable without approval, the allowlist accepts exactly one
statement shape, and no read-only server can expose the mutation tools. One test spawns the real
server (local, no cloud calls)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from edge import gate, mcp
from edge.config import USECASE_DIR

ZONE = "zone-test-0123"
RULE = (
    '{"action":"block","ratelimit":{"characteristics":["ip.src","cf.colo.id"],"period":10,'
    '"requests_per_period":%d,"mitigation_timeout":10},'
    '"expression":"(starts_with(http.request.uri.path, \\"/\\"))",'
    '"description":"%s rate limit (managed by stackql-deploy)","enabled":true}'
)


def replace_stmt(
    threshold: int = 30, prefix: str = "agentic-demo", zone: str = ZONE, phase="http_ratelimit"
):
    return (
        f"REPLACE cloudflare.rulesets.phases SET rules = '[{RULE % (threshold, prefix)}]' "
        f"WHERE zone_id = '{zone}' AND ruleset_phase = '{phase}'"
    )


# --- servers ------------------------------------------------------------------------------------


def test_read_only_factory_refuses_mutation_tools():
    with pytest.raises(ValueError):
        mcp.stackql_server(
            mode="read_only", allowed_tools=("run_select_query", "run_mutation_query")
        )


def test_unknown_mode_refused():
    with pytest.raises(ValueError):
        mcp.stackql_server(mode="safe")


def test_read_only_filter_blocks_mutation_and_admin_tools():
    server = mcp.read_only_server("test-ro")
    f = server.tool_filter
    allowed = (
        set(f.get("allowed_tool_names") or [])
        if isinstance(f, dict)
        else set(getattr(f, "allowed_tool_names", []) or [])
    )
    blocked = (
        set(f.get("blocked_tool_names") or [])
        if isinstance(f, dict)
        else set(getattr(f, "blocked_tool_names", []) or [])
    )
    assert allowed == set(mcp.READ_TOOLS)
    assert set(mcp.MUTATION_TOOLS) <= blocked and set(mcp.ADMIN_TOOLS) <= blocked


def _binary_cached() -> bool:
    if os.environ.get("STACKQL_MCP_BIN"):
        return True
    return (Path.home() / ".stackql" / "mcp-server-bin").exists()


@pytest.mark.skipif(not _binary_cached(), reason="stackql binary not cached; would download")
@pytest.mark.asyncio
async def test_read_only_server_hides_mutation_tools_and_publishes_instructions():
    async with mcp.read_only_server("test-ro") as server:
        names = {t.name for t in await server.list_tools()}
        text = await mcp.read_server_instructions(server, "stackql://docs/instructions")
    assert "run_select_query" in names
    assert not (names & set(mcp.MUTATION_TOOLS)), names
    assert not (names & set(mcp.ADMIN_TOOLS)), names
    assert text and "query_library_search" in text


# --- the allowlist ------------------------------------------------------------------------------


def test_allowlist_accepts_the_one_replace_shape():
    sql = gate.check_statement("set_rate_limit_threshold", replace_stmt(30) + ";", threshold=30)
    assert sql == replace_stmt(30)
    # case and whitespace in the keywords do not matter, the pins do
    loose = replace_stmt(100).replace("REPLACE", "replace").replace(" WHERE ", "\nwhere ")
    assert gate.check_statement("set_rate_limit_threshold", loose, threshold=100)


@pytest.mark.parametrize(
    "sql, why",
    [
        (
            replace_stmt(30).replace("cloudflare.rulesets.phases", "cloudflare.rulesets.rulesets"),
            "other resource",
        ),
        (replace_stmt(30).replace("REPLACE", "UPDATE"), "other verb"),
        (replace_stmt(30) + "; " + replace_stmt(30), "two statements"),
        (
            replace_stmt(30) + "; DELETE FROM cloudflare.zones.zones WHERE zone_id = 'x'",
            "trailing verb",
        ),
        (
            "SELECT id FROM cloudflare.rulesets.phases WHERE zone_id = 'z' AND ruleset_phase = 'http_ratelimit'",
            "a SELECT",
        ),
        (replace_stmt(30, zone="some-other-zone"), "wrong zone"),
        (replace_stmt(30, phase="http_request_firewall_custom"), "wrong phase"),
        (replace_stmt(30) + " AND id = 'x'", "extra WHERE term"),
        (replace_stmt(30).replace("AND ruleset_phase = 'http_ratelimit'", ""), "missing phase pin"),
        (replace_stmt(30, prefix="production"), "description without DEMO_PREFIX"),
        (replace_stmt(31), "threshold outside the policy"),
        (replace_stmt(100), "threshold not the one decided"),
        (
            replace_stmt(30).replace(
                "SET rules = '[", 'SET rules = \'[{"description":"agentic-demo x"},['
            ),
            "not exactly one rule",
        ),
        (replace_stmt(30).replace("SET rules = '[", "SET rules = 'oops ["), "rules not JSON"),
        (replace_stmt(30) + " -- comment", "comment"),
        ("", "empty"),
        (replace_stmt(30).replace("'http_ratelimit'", "'http_ratelimit"), "unbalanced quote"),
    ],
)
def test_allowlist_rejects(sql, why):
    with pytest.raises(gate.StatementRejected):
        gate.check_statement("set_rate_limit_threshold", sql, threshold=30)


def test_allowlist_keywords_inside_the_json_literal_do_not_count():
    sql = replace_stmt(30).replace("rate limit (managed", "rate limit; select delete drop (managed")
    assert gate.check_statement("set_rate_limit_threshold", sql, threshold=30)


def test_allowlist_unknown_action():
    with pytest.raises(gate.StatementRejected):
        gate.check_statement("delete_everything", replace_stmt(30), threshold=30)


def test_verification_select_is_pinned_and_flat():
    ok = "SELECT JSON_EXTRACT(rules, '$[0].ratelimit.requests_per_period') AS t FROM cloudflare.rulesets.phases WHERE zone_id = 'zone-test-0123' AND ruleset_phase = 'http_ratelimit'"
    assert gate.check_verification_select(ok + ";") == ok
    for bad in (
        ok.replace("zone-test-0123", "other"),
        ok.replace("cloudflare.rulesets.phases", "cloudflare.zones.zones"),
        "WITH x AS (" + ok + ") SELECT * FROM x",
        ok + "; " + ok,
        replace_stmt(30),
    ):
        with pytest.raises(gate.StatementRejected):
            gate.check_verification_select(bad)


def test_issue_insert_is_pinned_to_the_decision_repo():
    sql = "INSERT INTO github.issues.issues (owner, repo, title, body, labels) SELECT 'acme', 'ops', 't', 'b; DROP', '[]'"
    assert gate.check_statement("file_decision_issue", sql, owner="acme", repo="ops")
    with pytest.raises(gate.StatementRejected):
        gate.check_statement("file_decision_issue", sql, owner="acme", repo="other")
    with pytest.raises(gate.StatementRejected):
        gate.check_statement(
            "file_decision_issue",
            sql.replace("github.issues.issues", "github.repos.repos"),
            owner="acme",
            repo="ops",
        )
    with pytest.raises(gate.StatementRejected):
        gate.check_statement("file_decision_issue", sql + "; " + sql, owner="acme", repo="ops")


# --- preparing proposals ------------------------------------------------------------------------


def test_prepare_proposed_checks_statement_and_verification():
    verify = "SELECT id FROM cloudflare.rulesets.phases WHERE zone_id = 'zone-test-0123' AND ruleset_phase = 'http_ratelimit'"
    pending = gate.prepare_proposed(
        "set_rate_limit_threshold", replace_stmt(30), "prop-1", threshold=30, verify_sql=verify
    )
    assert pending.source == "model" and pending.sql == replace_stmt(30)
    assert pending.verify_sql == verify and len(pending.nonce) == 16
    with pytest.raises(gate.StatementRejected, match="no statement was proposed"):
        gate.prepare_proposed("set_rate_limit_threshold", "", "p", threshold=30)
    with pytest.raises(gate.StatementRejected):
        gate.prepare_proposed(
            "set_rate_limit_threshold",
            replace_stmt(30),
            "p",
            threshold=30,
            verify_sql="DELETE FROM x",
        )
    with pytest.raises(gate.StatementRejected):
        gate.prepare_proposed(
            "file_decision_issue", "INSERT INTO github.issues.issues (owner) SELECT 'a'", "p"
        )


def test_prepare_code_renders_and_checks_the_restore_statement():
    pending = gate.prepare_code(
        "set_rate_limit_threshold", {"threshold": 100, "demo_prefix": "agentic-demo"}, "prop-r"
    )
    assert pending.source == "edge/restore_rate_limit"
    assert '"requests_per_period":100' in pending.sql
    with pytest.raises(gate.StatementRejected, match="outside the policy"):
        gate.prepare_code("set_rate_limit_threshold", {"threshold": 5, "demo_prefix": "d"}, "p")
    with pytest.raises(gate.StatementRejected):
        gate.prepare_code("set_rate_limit_threshold", {"threshold": "lots"}, "p")
    with pytest.raises(gate.StatementRejected):
        gate.prepare_code("delete_everything", {}, "prop-x")


@pytest.mark.asyncio
async def test_execute_requires_matching_approval():
    pending = gate.prepare_proposed(
        "set_rate_limit_threshold", replace_stmt(30), "prop-t", threshold=30
    )

    async def never(_server):
        raise AssertionError("no server may start without a matching approval")

    with pytest.raises(gate.ApprovalDenied):
        await gate.execute_approved(pending, None, assert_target=never)
    wrong_nonce = gate.Approval("prop-t", "not-the-nonce", "tester", "terminal")
    with pytest.raises(gate.ApprovalDenied):
        await gate.execute_approved(pending, wrong_nonce, assert_target=never)
    other_proposal = gate.Approval("prop-other", pending.nonce, "tester", "terminal")
    with pytest.raises(gate.ApprovalDenied):
        await gate.execute_approved(pending, other_proposal, assert_target=never)


def test_terminal_gate_declines_on_wrong_phrase(monkeypatch):
    pending = gate.prepare_code(
        "set_rate_limit_threshold", {"threshold": 100, "demo_prefix": "agentic-demo"}, "prop-t2"
    )
    monkeypatch.setattr("builtins.input", lambda: "yes")
    assert gate.ask_terminal(pending) is None
    monkeypatch.setattr("builtins.input", lambda: "approve prop-t2")
    approval = gate.ask_terminal(pending)
    assert approval is not None and approval.nonce == pending.nonce


def test_mutation_tools_named_only_in_gate_mcp_and_costs():
    """Only mcp.py (definitions), gate.py (the executor) and costs.py (counts them by name) may
    name the mutation tools or the full_access mode."""
    offenders = []
    for p in (USECASE_DIR / "edge").glob("*.py"):
        if p.name in ("mcp.py", "gate.py", "costs.py"):
            continue
        text = p.read_text(encoding="utf-8")
        if re.search(r"[\"'](full_access|run_mutation_query|run_lifecycle_operation)[\"']", text):
            offenders.append(p.name)
    assert not offenders, offenders
