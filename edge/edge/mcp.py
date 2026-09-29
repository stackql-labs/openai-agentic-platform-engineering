"""StackQL MCP server factory for the OpenAI Agents SDK.

The server is the PyPI package stackql-mcp-server (console script `stackql-mcp` in this project's
venv), spawned over stdio by MCPServerStdio. Extra arguments pass straight through to the stackql
binary; later duplicate flags win, so `--approot` and `--mcp.config` given here override the
launcher's defaults.

Tool surface on stackql v0.12.718 (verified with server_info):

  discovery : list_providers, list_registry, list_services, list_resources, list_methods,
              describe_resource, describe_method
  execution : run_select_query, validate_select_query,
              run_mutation_query, run_lifecycle_operation        <- gated (see below)
  library   : query_library_search, query_library_get
  admin     : server_info, pull_provider, reload_credentials

Two server modes are used and they are the enforcement, not documentation:

  read_only   : the server refuses every write. Every model-facing server runs in this mode, and a
                static tool filter also hides the mutation and admin tools, so the model never sees
                them. Even a prompt-injected "REPLACE ..." cannot execute.
  full_access : writes are allowed by the server (each one lands in the audit file). Only
                edge/gate.py constructs a server in this mode, and it never hands it to a model:
                the approved statement is sent by code. (The server's own `safe` mode needs an
                elicitation-capable MCP client, which the Agents SDK client is not.)
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from agents.mcp import MCPServerStdio, create_static_tool_filter
from rich.markup import escape

from .config import REPO_ROOT, settings
from .costs import console

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
MODES = ("read_only", "full_access")


def stackql_mcp_command() -> str:
    """The `stackql-mcp` console script from this project's venv (PyPI stackql-mcp-server)."""
    candidate = Path(sys.executable).parent / "stackql-mcp"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("stackql-mcp")
    if found:
        return found
    raise RuntimeError(
        "stackql-mcp not found - run `uv sync` in edge/ (package stackql-mcp-server)"
    )


def mcp_args(mode: str, audit_log: Path | None) -> list[str]:
    s = settings()
    cfg: dict[str, Any] = {"server": {"transport": "stdio", "mode": mode}}
    if audit_log is not None:
        audit_log.parent.mkdir(parents=True, exist_ok=True)
        cfg["server"]["audit"] = {"file": {"path": audit_log.as_posix()}}
    return ["--approot", s.stackql_approot.as_posix(), "--mcp.config", json.dumps(cfg)]


class StackQLServer(MCPServerStdio):
    """MCPServerStdio that echoes every statement to the console as it runs - the point of the
    demo is that each step is a reviewable SQL statement, whether a model or the gate sent it."""

    def __init__(self, *args: Any, label: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.label = label

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None):
        args = arguments or {}
        sql = args.get("sql")
        label = escape(self.label)
        if sql:
            console.print(f"[dim]{label}[/dim] [bold]{tool_name}[/bold]")
            console.print(f"[cyan]{escape(str(sql))}[/cyan]")
        else:
            shown = {k: v for k, v in args.items() if k != "format"}
            console.print(f"[dim]{label}[/dim] [bold]{tool_name}[/bold] {escape(str(shown or ''))}")
        return await super().call_tool(tool_name, arguments)


def stackql_server(
    *,
    name: str = "stackql",
    mode: str = "read_only",
    allowed_tools: tuple[str, ...] | None = None,
    timeout_seconds: float = 180.0,
) -> StackQLServer:
    """Build a StackQL MCP server. Default: read_only with mutation and admin tools hidden.

    Use as `async with stackql_server() as server:`. Only edge.gate may pass mode="full_access".
    """
    if mode not in MODES:
        raise ValueError(f"unsupported mcp mode {mode!r} (one of {MODES})")
    allowed = tuple(allowed_tools) if allowed_tools is not None else READ_TOOLS
    if mode == "read_only" and set(allowed) & set(MUTATION_TOOLS):
        raise ValueError("mutation tools cannot be exposed on a read_only server")
    blocked = None
    if mode == "read_only":
        blocked = [t for t in MUTATION_TOOLS + ADMIN_TOOLS if t not in allowed]
    s = settings()
    return StackQLServer(
        label=f"[{name} {mode}]",
        name=name,
        params={
            "command": stackql_mcp_command(),
            "args": mcp_args(mode, s.mcp_audit_log),
            "env": dict(os.environ),
            "cwd": str(REPO_ROOT),
        },
        cache_tools_list=True,
        client_session_timeout_seconds=timeout_seconds,
        tool_filter=create_static_tool_filter(
            allowed_tool_names=list(allowed), blocked_tool_names=blocked
        ),
    )


def read_only_server(name: str = "stackql-ro") -> StackQLServer:
    return stackql_server(name=name, mode="read_only")


# --- result helpers ----------------------------------------------------------------------------


def tool_text(result: Any) -> str:
    return "\n".join(getattr(c, "text", "") or "" for c in (getattr(result, "content", None) or []))


def tool_is_error(result: Any) -> bool:
    return bool(getattr(result, "isError", False) or getattr(result, "is_error", False))


def _unwrap_rows(value: Any) -> list[dict]:
    """Pull the rows list out of the shapes a StackQL tool result arrives in."""
    if isinstance(value, list):
        if value and all(isinstance(v, dict) and "text" in v for v in value):
            # a list of text blocks (the SDK's rendering of a multi-part tool result)
            return [r for v in value for r in _unwrap_rows(v)]
        return [r for r in value if isinstance(r, dict)]
    if isinstance(value, dict):
        if isinstance(value.get("rows"), list):
            return _unwrap_rows(value["rows"])
        if isinstance(value.get("text"), str):
            return _unwrap_rows(value["text"])
        return []
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("{", "[")):
            try:
                return _unwrap_rows(json.loads(text))
            except json.JSONDecodeError:
                return []
    return []


def tool_rows(result: Any) -> list[dict]:
    """Rows from a run_select_query result: structuredContent.rows, else the text parsed as JSON."""
    structured = getattr(result, "structuredContent", None) or getattr(
        result, "structured_content", None
    )
    rows = _unwrap_rows(structured)
    if rows:
        return rows
    return _unwrap_rows(tool_text(result))


def rows_from_output(output: Any) -> list[dict]:
    """Rows from a ToolCallOutputItem.output (whatever the SDK serialised for the model)."""
    return _unwrap_rows(output)
