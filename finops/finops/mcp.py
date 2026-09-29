"""StackQL MCP server factory for the OpenAI Agents SDK.

The server is the PyPI `stackql-mcp-server` launcher (console script `stackql-mcp`, installed in
this project's own venv) started by MCPServerStdio as a stdio subprocess. Extra arguments pass
straight through to the stackql binary and later duplicate flags win, so `--approot` and
`--mcp.config` below override the launcher's defaults.

Tool surface, verified with `server_info` against stackql v0.12.718:

  discovery : list_providers, list_registry, list_services, list_resources, list_methods,
              describe_resource, describe_method
  execution : run_select_query, validate_select_query,
              run_mutation_query, run_lifecycle_operation        <- never reachable here
  library   : query_library_search, query_library_get
  admin     : server_info, pull_provider, reload_credentials

Enforcement, not documentation: every server this use case starts runs in `read_only` mode
(the server refuses every write), and a static tool filter hides the mutation and admin tools
from the models on top of that. There is no mutation path in this use case at all: the DELETE
and EXEC statements it produces are drafted for human review only. The models use the
discovery and library tools to find resources and IO contracts; nothing is pre-scripted.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from agents.mcp import MCPServerStdio, create_static_tool_filter

from .config import REPO_ROOT, settings

MUTATION_TOOLS = ("run_mutation_query", "run_lifecycle_operation")
ADMIN_TOOLS = ("pull_provider", "reload_credentials")
READ_TOOLS = (
    "server_info",
    "list_providers",
    "list_services",
    "list_resources",
    "list_methods",
    "describe_resource",
    "describe_method",
    "run_select_query",
    "validate_select_query",
    "query_library_search",
    "query_library_get",
)
MODE = "read_only"


def launcher_command() -> str:
    """The `stackql-mcp` console script from this project's venv (PyPI stackql-mcp-server)."""
    candidates = [Path(sys.executable).parent / "stackql-mcp", shutil.which("stackql-mcp")]
    for c in candidates:
        if c and Path(c).exists():
            return str(c)
    raise RuntimeError("stackql-mcp launcher not found - run `uv sync` inside finops/")


def server_config() -> dict[str, Any]:
    s = settings()
    s.mcp_audit_log.parent.mkdir(parents=True, exist_ok=True)
    return {
        "server": {
            "transport": "stdio",
            "mode": MODE,
            "audit": {"file": {"path": s.mcp_audit_log.as_posix()}},
        }
    }


def launcher_args() -> list[str]:
    s = settings()
    return ["--approot", s.stackql_approot.as_posix(), "--mcp.config", json.dumps(server_config())]


def read_only_server(
    name: str = "stackql-ro", *, timeout_seconds: float = 300.0, filtered: bool = True
) -> MCPServerStdio:
    """Build an MCPServerStdio for StackQL in read_only mode.

    filtered=True (the models' server) hides the mutation and admin tools. filtered=False is
    used only by the `setup` and `validate` subcommands, which call server_info, pull_provider
    and validate_select_query directly through the server object - no model is attached.
    The server mode is read_only either way.
    """
    if filtered:
        tool_filter = create_static_tool_filter(
            allowed_tool_names=list(READ_TOOLS),
            blocked_tool_names=list(MUTATION_TOOLS + ADMIN_TOOLS),
        )
    else:
        tool_filter = None
    return MCPServerStdio(
        name=name,
        params={
            "command": launcher_command(),
            "args": launcher_args(),
            "env": dict(os.environ),
            "cwd": str(REPO_ROOT),
        },
        cache_tools_list=True,
        client_session_timeout_seconds=timeout_seconds,
        tool_filter=tool_filter,
    )


def assert_read_only(server: MCPServerStdio) -> None:
    """Guard used by tests and at startup: a model-facing server must be read_only with the
    mutation tools filtered."""
    args = list(server.params.args)
    cfg = json.loads(args[args.index("--mcp.config") + 1])
    if cfg["server"]["mode"] != MODE:
        raise RuntimeError(f"model-facing server must be {MODE}, got {cfg['server']['mode']}")
    tf = server.tool_filter if isinstance(server.tool_filter, dict) else {}
    blocked = set(tf.get("blocked_tool_names") or [])
    allowed = set(tf.get("allowed_tool_names") or [])
    if not set(MUTATION_TOOLS) <= blocked or allowed & set(MUTATION_TOOLS):
        raise RuntimeError(
            "model-facing server must block run_mutation_query and run_lifecycle_operation"
        )


def result_payload(res: Any) -> Any:
    """structuredContent if the server sent it, else the text block parsed as JSON, else text."""
    sc = getattr(res, "structured_content", None)
    if sc:
        return sc
    texts = [getattr(b, "text", "") for b in (getattr(res, "content", None) or [])]
    text = "\n".join(t for t in texts if t)
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


async def call(server: MCPServerStdio, tool: str, arguments: dict[str, Any] | None = None) -> Any:
    """Call a tool directly through the server object (setup/validate paths, no model)."""
    res = await server.call_tool(tool, arguments or {})
    payload = result_payload(res)
    if getattr(res, "is_error", False):
        raise RuntimeError(f"{tool} failed: {payload}")
    return payload
