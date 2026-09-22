"""The universal-interface hook (and the Phase 0 smoke test): one agent, one MCP server, one SQL
dialect, four cloud control planes. Lists providers through the MCP server and runs one SELECT per
configured provider from the canonical smoke queries, then prints the cost/trace summary.

    uv run python -m oape_agents.smoke [--fallback] [--record]
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from agents import Runner, gen_trace_id, trace
from pydantic import BaseModel

from oape_agents.common.config import provider_configured, settings
from oape_agents.common.costs import RunLedger, console
from oape_agents.common.mcp import read_only_server
from oape_agents.common.queries import list_queries
from oape_agents.common.runlog import banner, load_fallback, save_fallback
from oape_agents.common.tiers import make_agent


class SmokeResult(BaseModel):
    providers_seen: list[str]
    queries_run: int
    rows_by_query: list[str]
    notes: str


INSTRUCTIONS = """You are a smoke-test agent. Do exactly this:
1. Call list_providers once and note the provider names.
2. Run each SQL statement you are given with run_select_query, once each, verbatim. Do not
   rewrite them. Record how many rows each returned, as "<query id>: <n> rows".
3. Return the structured result. Do not call any other tool."""


def _print(out: SmokeResult) -> None:
    console.print(f"providers available through one MCP server: {len(out.providers_seen)}")
    for line in out.rows_by_query:
        console.print(f"  {line}")
    console.print(out.notes)


async def main(record: bool) -> int:
    s = settings()
    banner(
        "universal interface",
        "Agents SDK -> StackQL MCP (read_only) -> one SELECT per configured provider",
    )
    all_q = list_queries("smoke")
    queries = [q for q in all_q if all(provider_configured(p) for p in q.providers)]
    skipped = [q.id for q in all_q if q not in queries]
    if skipped:
        console.print(f"skipping (credentials not configured): {skipped}")
    sql_block = "\n\n".join(f"-- {q.id}\n{q.render()}" for q in queries)
    ledger = RunLedger("smoke", trace_id=gen_trace_id())
    async with read_only_server() as server:
        agent = make_agent(
            "sweep",
            name="smoke",
            instructions=INSTRUCTIONS,
            mcp_servers=[server],
            output_type=SmokeResult,
        )
        with trace("oape smoke", trace_id=ledger.trace_id):
            result = await Runner.run(agent, f"Statements to run:\n\n{sql_block}", max_turns=20)
        ledger.record("smoke", s.sweep.model, result)
    out: SmokeResult = result.final_output
    _print(out)
    ledger.print_summary()
    payload = {"result": out.model_dump(), "ledger": ledger.to_dict()}
    ledger.save(payload)
    if record:
        console.print(f"recorded fallback: {save_fallback('smoke', payload)}")
    return 0


def replay() -> int:
    data = load_fallback("smoke")
    banner("universal interface (replayed)", f"recorded {data.get('recorded_at')}")
    _print(SmokeResult.model_validate(data["result"]))
    ledger = RunLedger("smoke", trace_id=data.get("ledger", {}).get("trace_id"))
    for e in data.get("ledger", {}).get("entries", []):
        ledger.record_dict(e)
    ledger.print_summary(replay=True)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fallback", action="store_true")
    ap.add_argument("--record", action="store_true")
    a = ap.parse_args()
    sys.exit(replay() if a.fallback else asyncio.run(main(a.record)))
