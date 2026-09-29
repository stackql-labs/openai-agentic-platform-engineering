"""No model-facing server can expose a mutation tool: the factory has no mode parameter, the
config it renders is read_only, the static filter blocks the mutation and admin tools, and the
guard rejects a server built any other way. No server is started here."""

from __future__ import annotations

import inspect
import json

import pytest
from agents.mcp import MCPServerStdio, create_static_tool_filter

from finops import mcp
from finops.config import settings


def _config_of(server: MCPServerStdio) -> dict:
    args = list(server.params.args)
    return json.loads(args[args.index("--mcp.config") + 1])


def test_factory_cannot_take_a_mode():
    assert "mode" not in inspect.signature(mcp.read_only_server).parameters
    assert mcp.MODE == "read_only"


def test_model_facing_server_is_read_only_with_mutation_tools_hidden():
    server = mcp.read_only_server("t")
    cfg = _config_of(server)
    assert cfg["server"]["mode"] == "read_only"
    assert cfg["server"]["audit"]["file"]["path"] == settings().mcp_audit_log.as_posix()
    assert list(server.params.args)[:2] == ["--approot", settings().stackql_approot.as_posix()]
    assert server.params.command.endswith("stackql-mcp")
    blocked = set(server.tool_filter["blocked_tool_names"])
    allowed = set(server.tool_filter["allowed_tool_names"])
    assert set(mcp.MUTATION_TOOLS) | set(mcp.ADMIN_TOOLS) <= blocked
    assert not allowed & (set(mcp.MUTATION_TOOLS) | set(mcp.ADMIN_TOOLS))
    assert "run_select_query" in allowed and "validate_select_query" in allowed
    mcp.assert_read_only(server)


def test_unfiltered_setup_server_is_still_read_only():
    server = mcp.read_only_server("setup", filtered=False)
    assert _config_of(server)["server"]["mode"] == "read_only"
    assert server.tool_filter is None


def test_guard_rejects_a_writable_or_unfiltered_server():
    good = mcp.read_only_server("t")
    bad_args = list(good.params.args)
    bad_args[bad_args.index("--mcp.config") + 1] = json.dumps(
        {"server": {"transport": "stdio", "mode": "full_access"}}
    )
    writable = MCPServerStdio(
        name="bad",
        params={**good.params.model_dump(), "args": bad_args},
        tool_filter=good.tool_filter,
    )
    with pytest.raises(RuntimeError, match="read_only"):
        mcp.assert_read_only(writable)
    exposed = MCPServerStdio(
        name="bad2",
        params=good.params.model_dump(),
        tool_filter=create_static_tool_filter(
            allowed_tool_names=list(mcp.READ_TOOLS) + ["run_mutation_query"]
        ),
    )
    with pytest.raises(RuntimeError, match="block"):
        mcp.assert_read_only(exposed)
    with pytest.raises(RuntimeError, match="block"):
        mcp.assert_read_only(mcp.read_only_server("setup", filtered=False))


def test_launcher_is_the_pypi_console_script():
    assert mcp.launcher_command().endswith("stackql-mcp")


def test_result_payload_prefers_structured_content():
    class Block:
        text = '{"rows": [1]}'

    class Res:
        structured_content = None
        content = [Block()]

    assert mcp.result_payload(Res()) == {"rows": [1]}
    Res.structured_content = {"valid": True}
    assert mcp.result_payload(Res()) == {"valid": True}
