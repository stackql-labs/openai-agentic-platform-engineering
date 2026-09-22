"""`make setup` final step: start the StackQL MCP server exactly as the agents do, call
server_info, list the tool surface, and report which providers have credentials configured.
No model call, no cloud API call."""

from __future__ import annotations

import asyncio
import sys

from oape_agents.common.config import PROVIDER_ENV, provider_configured, settings
from oape_agents.common.costs import console
from oape_agents.common.mcp import MUTATION_TOOLS, read_only_server


async def main() -> int:
    s = settings()
    console.rule("[bold]verify: StackQL MCP server[/bold]")
    async with read_only_server("verify") as server:
        tools = await server.list_tools()
        names = sorted(t.name for t in tools)
        info = await server.call_tool("server_info", {})
        text = info.content[0].text if info.content else ""
    console.print(text.strip())
    console.print(f"tools visible to read-only agents ({len(names)}): {', '.join(names)}")
    hidden = [t for t in MUTATION_TOOLS if t in names]
    if hidden:
        console.print(f"[red]mutation tools visible on a read-only server: {hidden}[/red]")
        return 1
    console.print("mutation tools hidden from read-only agents: ok")
    console.print(f"approot: {s.stackql_approot}")
    for p in PROVIDER_ENV:
        state = "configured" if provider_configured(p) else "not configured"
        console.print(f"  {p:<13} {state}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
