"""The mutation path is provably unreachable without approval.

These tests need the stackql binary (local, no cloud calls) for the tool-surface checks and no
network for the rest."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from oape_agents.common import mcp
from oape_agents.triage import gate

ROOT = Path(__file__).resolve().parents[1]


def test_read_only_factory_refuses_mutation_tools():
    with pytest.raises(ValueError):
        mcp.stackql_mcp_server(
            mode="read_only", allowed_tools=("run_select_query", "run_mutation_query")
        )


def test_unknown_mode_refused():
    with pytest.raises(ValueError):
        mcp.stackql_mcp_server(mode="safe")


@pytest.mark.asyncio
async def test_read_only_server_hides_mutation_tools():
    async with mcp.read_only_server("test-ro") as server:
        names = {t.name for t in await server.list_tools()}
    assert "run_select_query" in names
    assert not (names & set(mcp.MUTATION_TOOLS)), names
    assert not (names & set(mcp.ADMIN_TOOLS)), names


@pytest.mark.asyncio
async def test_execute_requires_matching_approval():
    pending = gate.prepare("scale_out_by_one", {"desired_capacity": "2"}, "prop-test")
    with pytest.raises(gate.ApprovalDenied):
        await gate.execute_approved(pending, None)
    wrong = gate.Approval("prop-test", "not-the-nonce", "tester", "terminal")
    with pytest.raises(gate.ApprovalDenied):
        await gate.execute_approved(pending, wrong)
    other = gate.Approval("prop-other", pending.nonce, "tester", "terminal")
    with pytest.raises(gate.ApprovalDenied):
        await gate.execute_approved(pending, other)


def test_only_menu_actions_can_be_prepared():
    with pytest.raises(gate.ApprovalDenied):
        gate.prepare("delete_everything", {}, "prop-x")


def test_mutation_tools_referenced_only_in_gate_and_mcp():
    """No module other than mcp.py (definitions) and triage/gate.py may name the mutation tools
    or the full_access mode. The cost ledger counts them by name, which is the one allowed reader."""
    offenders = []
    for p in (ROOT / "oape_agents").rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel.endswith(("common/mcp.py", "triage/gate.py", "common/costs.py")):
            continue
        text = p.read_text(encoding="utf-8")
        if re.search(r"[\"'](full_access|run_mutation_query|run_lifecycle_operation)[\"']", text):
            offenders.append(rel)
    assert not offenders, offenders


def test_terminal_gate_declines_on_wrong_phrase(monkeypatch):
    pending = gate.prepare("scale_out_by_one", {"desired_capacity": "2"}, "prop-t2")
    monkeypatch.setattr("builtins.input", lambda: "yes")
    assert gate.ask_terminal(pending) is None
    monkeypatch.setattr("builtins.input", lambda: "approve prop-t2")
    approval = gate.ask_terminal(pending)
    assert approval is not None and approval.nonce == pending.nonce
