"""StackQL MCP server factory for the OpenAI Agents SDK.

Tool surface, verified with `server_info` against stackql v0.12.718 (2026-09-22):

  discovery : list_providers, list_registry, list_services, list_resources, list_methods,
              describe_resource, describe_method
  execution : run_select_query, validate_select_query,
              run_mutation_query, run_lifecycle_operation        <- gated (see below)
  library   : query_library_search, query_library_get
  admin     : server_info, pull_provider, reload_credentials

server_info fields: version, commit, build_date, platform, transport, mode, sql_backend,
provider_registry, is_read_only. Names can vary by version - re-verify with server_info.

Two server modes are used here and they are the enforcement, not documentation:

  read_only : the server refuses every write. Used by every agent except the triage
              executor. Even a prompt-injected "DELETE FROM ..." cannot execute.
  full_access : writes are allowed by the server (each one is written to the audit file).
              The approval gate lives in oape_agents/triage/gate.py, which is the only code
              that constructs a server in this mode and the only code that exposes the two
              mutation tools to a model. The gate is the elicitation step: the server's own
              `safe` mode needs an elicitation-capable MCP client (an IDE), which the Agents
              SDK client is not, so `safe` refuses every write from this code path (verified).

On top of the server mode, a static tool filter hides run_mutation_query and
run_lifecycle_operation from read-only agents, so the model never even sees them.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from agents.mcp import MCPServerStdio, create_static_tool_filter

from .config import ENV_FILE, settings

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


def stackql_command() -> str:
    s = settings()
    path = shutil.which(s.stackql_bin) or s.stackql_bin
    if not Path(path).exists():
        raise RuntimeError(f"stackql binary not found: {s.stackql_bin} (set STACKQL_BIN)")
    return path


def mcp_args(mode: str, audit_log: Path | None) -> list[str]:
    s = settings()
    server_cfg: dict = {"server": {"transport": "stdio", "mode": mode}}
    if audit_log is not None:
        audit_log.parent.mkdir(parents=True, exist_ok=True)
        server_cfg["server"]["audit"] = {"file": {"path": audit_log.as_posix()}}
    args = [
        "mcp",
        "--mcp.server.type=stdio",
        "--approot",
        s.stackql_approot.as_posix(),
        "--mcp.config",
        json.dumps(server_cfg),
    ]
    if ENV_FILE.exists():
        args += ["--env.file", ENV_FILE.as_posix()]
    return args


def stackql_mcp_server(
    *,
    name: str = "stackql",
    mode: str = "read_only",
    allowed_tools: tuple[str, ...] | None = None,
    timeout_seconds: float = 180.0,
) -> MCPServerStdio:
    """Build an MCPServerStdio for StackQL. Default is read-only with mutation tools hidden.

    Use as `async with stackql_mcp_server() as server:` and pass to Agent(mcp_servers=[server]).
    Only oape_agents.triage.gate may call this with mode="full_access".
    """
    if mode not in ("read_only", "full_access"):
        raise ValueError(f"unsupported mcp mode {mode!r}")
    if allowed_tools is None:
        allowed_tools = READ_TOOLS
    if mode == "read_only" and any(t in MUTATION_TOOLS for t in allowed_tools):
        raise ValueError("mutation tools cannot be exposed on a read_only server")
    s = settings()
    return MCPServerStdio(
        name=name,
        params={
            "command": stackql_command(),
            "args": mcp_args(mode, s.mcp_audit_log),
            "env": dict(os.environ),
            "cwd": str(ENV_FILE.parent),
        },
        cache_tools_list=True,
        client_session_timeout_seconds=timeout_seconds,
        tool_filter=create_static_tool_filter(
            allowed_tool_names=list(allowed_tools),
            blocked_tool_names=list(ADMIN_TOOLS) if mode == "read_only" else None,
        ),
    )


def read_only_server(name: str = "stackql-ro") -> MCPServerStdio:
    return stackql_mcp_server(name=name, mode="read_only")
