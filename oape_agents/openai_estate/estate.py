"""OpenAI estate closer: a short read-only sweep over the OpenAI organization - projects, service
account and API key age against OPENAI_KEY_MAX_AGE_DAYS, and spend and usage by project where the
Administration API exposes it. Output is a one-page governance brief.

    uv run python -m oape_agents.openai_estate.estate [--fallback] [--record]

Needs OPENAI_ADMIN_KEY (the openai_admin provider). Without it the run explains what is missing
and exits; `--fallback` replays fallbacks/openai_estate.json.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

from agents import Runner, gen_trace_id, trace
from pydantic import BaseModel, Field

from oape_agents.common.config import RUNS_DIR, provider_configured, settings
from oape_agents.common.costs import RunLedger, console
from oape_agents.common.mcp import read_only_server
from oape_agents.common.runlog import banner, load_fallback, save_fallback
from oape_agents.common.tiers import make_agent
from oape_agents.sweeps.base import render_query

QUERY_IDS = [
    "openai_estate/projects",
    "openai_estate/service_accounts",
    "openai_estate/api_keys",
    "openai_estate/costs_by_project",
    "openai_estate/usage_completions",
]


class KeyFinding(BaseModel):
    project: str
    owner: str
    key_or_account: str
    age_days: int
    last_used: str
    recommendation: str


class ProjectLine(BaseModel):
    project: str
    status: str
    keys: int
    service_accounts: int
    spend_usd: float
    note: str


class GovernanceBrief(BaseModel):
    org_summary: str = Field(description="Two or three sentences on the shape of the org")
    projects: list[ProjectLine]
    key_findings: list[KeyFinding]
    spend_summary: str
    actions: list[str] = Field(description="Ordered, concrete, each naming the project or key")


INSTRUCTIONS = """You are the governance sweep for the OpenAI organization itself: the same
always-on, read-only pattern the platform team runs on the clouds, pointed at the model provider.
Run the queries in the pack via run_select_query (render_query for the per-project ones: pass
project_id as a JSON array of every project id from openai_estate/projects, and cost_start_time as
the unix time given). Then write the one-page brief: projects with status, service accounts and
keys older than {max_age} day(s) (the demo threshold - report the real age), spend by project for
the window, and ordered actions. Keys with no recorded use are a finding. Do not call any other
tool. Never print key values; only ids, names and redacted values."""


async def run(record: bool) -> int:
    s = settings()
    banner(
        "OpenAI estate closer", "the org that runs the agents, governed by the same read-only sweep"
    )
    if not provider_configured("openai_admin"):
        console.print(
            "[yellow]OPENAI_ADMIN_KEY is not set. The openai_admin provider needs an Admin API key "
            "(Organization settings -> Admin keys). Add it to .env and re-run, or use --fallback.[/yellow]"
        )
        return 2
    start = str(int(time.time()) - 30 * 86400)
    from oape_agents.common.queries import load_query

    pack = "\n\n".join(
        f"### {qid}\n{load_query(qid).description}\n"
        + (
            f"```sql\n{load_query(qid).render()}\n```"
            if not any(p in ("project_id", "cost_start_time") for p in load_query(qid).params)
            else f"Dynamic parameters: {load_query(qid).params}. Use render_query."
        )
        for qid in QUERY_IDS
    )
    ledger = RunLedger("openai_estate", trace_id=gen_trace_id())
    async with read_only_server("stackql-openai") as server:
        agent = make_agent(
            "sweep",
            name="openai-estate",
            instructions=INSTRUCTIONS.format(max_age=s.openai_key_max_age_days),
            mcp_servers=[server],
            tools=[render_query],
            output_type=GovernanceBrief,
        )
        with trace("oape openai estate", trace_id=ledger.trace_id):
            result = await Runner.run(
                agent,
                f"cost_start_time (unix seconds, 30 days ago): {start}\n\nQuery pack:\n\n{pack}",
                max_turns=25,
            )
        ledger.record("estate sweep", s.sweep.model, result)
    brief: GovernanceBrief = result.final_output
    print_brief(brief)
    ledger.print_summary()
    payload = {"brief": brief.model_dump(), "ledger": ledger.to_dict()}
    ledger.save(payload)
    path = RUNS_DIR / "openai-estate-brief.md"
    path.write_text(to_markdown(brief), encoding="utf-8")
    console.print(f"brief written to {path}")
    if record:
        console.print(f"recorded fallback: {save_fallback('openai_estate', payload)}")
    return 0


def print_brief(b: GovernanceBrief) -> None:
    console.rule("governance brief")
    console.print(b.org_summary)
    for p in b.projects:
        console.print(
            f"  {p.project:<28} {p.status:<9} keys={p.keys} service_accounts={p.service_accounts} spend=USD {p.spend_usd:,.2f}  {p.note}"
        )
    if b.key_findings:
        console.rule("key age findings")
        for k in b.key_findings:
            console.print(
                f"  {k.project}: {k.key_or_account} ({k.owner}) age {k.age_days}d, last used {k.last_used} - {k.recommendation}"
            )
    console.print(b.spend_summary)
    console.rule("actions")
    for i, a in enumerate(b.actions, 1):
        console.print(f"  {i}. {a}")


def to_markdown(b: GovernanceBrief) -> str:
    lines = [
        "# OpenAI organization governance brief",
        "",
        b.org_summary,
        "",
        "## Projects",
        "",
        "| project | status | keys | service accounts | spend (USD) | note |",
        "|---|---|---|---|---|---|",
    ]
    for p in b.projects:
        lines.append(
            f"| {p.project} | {p.status} | {p.keys} | {p.service_accounts} | {p.spend_usd:,.2f} | {p.note} |"
        )
    lines += ["", "## Key age findings", ""]
    for k in b.key_findings:
        lines.append(
            f"- {k.project}: {k.key_or_account} ({k.owner}), age {k.age_days} days, last used {k.last_used}. {k.recommendation}"
        )
    lines += ["", "## Spend", "", b.spend_summary, "", "## Actions", ""]
    lines += [f"{i}. {a}" for i, a in enumerate(b.actions, 1)]
    return "\n".join(lines) + "\n"


def replay() -> int:
    data = load_fallback("openai_estate")
    banner(
        "OpenAI estate closer (replayed)",
        f"recorded {data.get('recorded_at')}"
        + (
            " - synthetic: no admin key was available when this fallback was written"
            if data.get("synthetic")
            else ""
        ),
    )
    print_brief(GovernanceBrief.model_validate(data["brief"]))
    ledger = RunLedger("openai_estate", trace_id=data.get("ledger", {}).get("trace_id"))
    for e in data.get("ledger", {}).get("entries", []):
        ledger.record_dict(e)
    ledger.print_summary(replay=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fallback", action="store_true")
    ap.add_argument("--record", action="store_true")
    a = ap.parse_args()
    if a.fallback:
        return replay()
    return asyncio.run(run(a.record))


if __name__ == "__main__":
    sys.exit(main())
