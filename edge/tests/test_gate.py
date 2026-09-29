"""The mutation path is provably unreachable without approval, and no read-only server can
expose the mutation tools. One test spawns the real server (local, no cloud calls)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from edge import gate, mcp
from edge.config import USECASE_DIR


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
async def test_read_only_server_hides_mutation_tools():
    async with mcp.read_only_server("test-ro") as server:
        names = {t.name for t in await server.list_tools()}
    assert "run_select_query" in names
    assert not (names & set(mcp.MUTATION_TOOLS)), names
    assert not (names & set(mcp.ADMIN_TOOLS)), names


def test_only_menu_actions_can_be_prepared():
    with pytest.raises(gate.ApprovalDenied):
        gate.prepare("delete_everything", {}, "prop-x")


def test_threshold_outside_policy_cannot_be_prepared():
    with pytest.raises(gate.ApprovalDenied, match="outside the policy"):
        gate.prepare("set_rate_limit_threshold", {"threshold": 5, "demo_prefix": "d"}, "p")
    with pytest.raises(gate.ApprovalDenied):
        gate.prepare("set_rate_limit_threshold", {"threshold": "lots"}, "p")


def test_prepare_renders_the_policy_threshold():
    pending = gate.prepare(
        "set_rate_limit_threshold", {"threshold": 30, "demo_prefix": "agentic-demo"}, "prop-1"
    )
    assert pending.query_id == "edge/tighten_rate_limit"
    assert '"requests_per_period":30' in pending.sql
    assert len(pending.nonce) == 16


@pytest.mark.asyncio
async def test_execute_requires_matching_approval():
    pending = gate.prepare(
        "set_rate_limit_threshold", {"threshold": 30, "demo_prefix": "agentic-demo"}, "prop-t"
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
    pending = gate.prepare(
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
