"""Drift briefing: delta between the two latest snapshots plus the tfstate comparison, classified
and narrated by the mini-tier model - on the deltas only, never the full estate.

    uv run python -m oape_agents.drift.brief [--no-snapshot] [--fallback] [--record]

With no changes and no drift the brief is one line and no model is called: the run costs the
snapshot's API calls and nothing else. That is the economics of hourly runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from typing import Literal

from agents import Runner, gen_trace_id, trace
from pydantic import BaseModel, Field

from oape_agents.common.config import settings
from oape_agents.common.costs import RunLedger, console
from oape_agents.common.queries import load_query
from oape_agents.common.runlog import banner, load_fallback, save_fallback
from oape_agents.common.tiers import make_agent
from oape_agents.drift import snapshot, terraform_state_reader


class Change(BaseModel):
    resource_type: str
    resource_key: str
    change: str = Field(description="added | removed | changed | drift-from-terraform")
    what_changed: str = Field(description="The attribute(s) and values, before -> after")
    classification: Literal["benign", "material"]
    reason: str


class DriftBrief(BaseModel):
    changes: list[Change]
    brief: str = Field(description="One paragraph in plain language for the platform team")


INSTRUCTIONS = """You are the hourly drift briefing for a demo cloud estate. You receive only the
deltas: rows that changed between two snapshots of the estate, and rows where live state differs
from the Terraform intended state. Classify each as benign (tag churn, naming, versioning or other
configuration with no security consequence) or material (network exposure, privilege, encryption,
anything that widens access) and write a one-paragraph brief. Cite resource ids. Do not speculate
about resources that are not in the input."""


def _diff(before: str | None, after: str | None) -> str:
    b = json.loads(before) if before else {}
    a = json.loads(after) if after else {}
    parts = []
    for k in sorted(set(b) | set(a)):
        if b.get(k) != a.get(k):
            parts.append(
                f"{k}: {json.dumps(b.get(k), default=str)} -> {json.dumps(a.get(k), default=str)}"
            )
    return "; ".join(parts) or "(no attribute differences)"


def run_delta(prev: dict, curr: dict) -> list[dict]:
    sql = load_query("drift/delta").render(prev_view=prev["table"], curr_view=curr["table"])
    con = sqlite3.connect(snapshot.db_path())
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(sql)]
    con.close()
    for r in rows:
        r["what_changed"] = _diff(r.get("before_attrs"), r.get("after_attrs"))
    return rows


def run_tfstate(curr: dict) -> list[dict]:
    try:
        terraform_state_reader.load(terraform_state_reader.read())
    except FileNotFoundError as e:
        console.print(f"[yellow]{e}[/yellow]")
        return []
    sql = load_query("drift/tfstate_vs_live").render(curr_view=curr["table"])
    con = sqlite3.connect(snapshot.db_path())
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(sql)]
    con.close()
    for r in rows:
        r["what_changed"] = _diff(r.get("intended_attrs"), r.get("live_attrs"))
    return rows


async def run(no_snapshot: bool, record: bool) -> int:
    s = settings()
    banner(
        "drift briefing", "snapshot -> delta (SQL over the local backend) -> brief on deltas only"
    )
    if not no_snapshot:
        snapshot.take("drift-brief")
    reg = snapshot.latest(2)
    if len(reg) < 2:
        console.print(
            "only one snapshot exists: take another later to see deltas; comparing tfstate only"
        )
        prev, curr = None, reg[-1]
    else:
        prev, curr = reg[0], reg[1]
    delta = run_delta(prev, curr) if prev else []
    drift = run_tfstate(curr)
    console.rule("deltas")
    console.print(
        f"snapshot {curr['ts']} vs {prev['ts'] if prev else '(none)'}: {len(delta)} change(s); "
        f"tfstate vs live: {len(drift)} drifted resource(s)"
    )
    for r in delta:
        console.print(
            f"  {r['change']:<8} {r['resource_type']} {r['resource_key']}: {r['what_changed'][:160]}"
        )
    for r in drift:
        console.print(
            f"  drift    {r['resource_type']} {r['resource_key']} ({r['tf_address']}): {r['what_changed'][:160]}"
        )
    ledger = RunLedger("drift", trace_id=None)
    payload: dict = {"prev": prev, "curr": curr, "delta": delta, "tfstate_drift": drift}
    if not delta and not drift:
        console.print(
            "[green]No changes since the previous snapshot and no drift from Terraform. Nothing to brief.[/green]"
        )
        ledger.notes.append("no deltas: no model call made")
        ledger.print_summary()
        payload["brief"] = None
        ledger.save(payload)
        if record:
            save_fallback("drift", {**payload, "ledger": ledger.to_dict()})
        return 0
    ledger.trace_id = gen_trace_id()
    inputs = {
        "snapshot_delta": [
            {
                k: r[k]
                for k in ("change", "provider", "resource_type", "resource_key", "what_changed")
            }
            for r in delta
        ],
        "tfstate_drift": [
            {
                "change": "drift-from-terraform",
                "resource_type": r["resource_type"],
                "resource_key": r["resource_key"],
                "tf_address": r["tf_address"],
                "what_changed": r["what_changed"],
            }
            for r in drift
        ],
    }
    agent = make_agent(
        "sweep",
        name="drift-brief",
        instructions=INSTRUCTIONS,
        output_type=DriftBrief,
        include_dialect_notes=False,
    )
    with trace("oape drift brief", trace_id=ledger.trace_id):
        result = await Runner.run(agent, json.dumps(inputs, indent=1), max_turns=3)
    ledger.record("brief (deltas only)", s.sweep.model, result)
    brief: DriftBrief = result.final_output
    console.rule("brief")
    for c in brief.changes:
        colour = "red" if c.classification == "material" else "cyan"
        console.print(
            f"[{colour}]{c.classification:<8}[/{colour}] {c.change} {c.resource_type} {c.resource_key}: {c.what_changed} - {c.reason}"
        )
    console.print(brief.brief)
    ledger.print_summary()
    payload["brief"] = brief.model_dump()
    payload["ledger"] = ledger.to_dict()
    ledger.save(payload)
    if record:
        console.print(f"recorded fallback: {save_fallback('drift', payload)}")
    return 0


def replay() -> int:
    data = load_fallback("drift")
    banner("drift briefing (replayed)", f"recorded {data.get('recorded_at')}")
    curr, prev = data.get("curr") or {}, data.get("prev") or {}
    console.rule("deltas")
    console.print(
        f"snapshot {curr.get('ts')} vs {prev.get('ts')}: {len(data.get('delta', []))} change(s); tfstate vs live: {len(data.get('tfstate_drift', []))}"
    )
    for r in data.get("delta", []):
        console.print(
            f"  {r['change']:<8} {r['resource_type']} {r['resource_key']}: {r['what_changed'][:160]}"
        )
    for r in data.get("tfstate_drift", []):
        console.print(
            f"  drift    {r['resource_type']} {r['resource_key']} ({r['tf_address']}): {r['what_changed'][:160]}"
        )
    b = data.get("brief")
    console.rule("brief")
    if not b:
        console.print(
            "[green]No changes since the previous snapshot and no drift from Terraform. Nothing to brief.[/green]"
        )
    else:
        for c in b["changes"]:
            colour = "red" if c["classification"] == "material" else "cyan"
            console.print(
                f"[{colour}]{c['classification']:<8}[/{colour}] {c['change']} {c['resource_type']} {c['resource_key']}: {c['what_changed']} - {c['reason']}"
            )
        console.print(b["brief"])
    ledger = RunLedger("drift", trace_id=data.get("ledger", {}).get("trace_id"))
    for e in data.get("ledger", {}).get("entries", []):
        ledger.record_dict(e)
    ledger.print_summary(replay=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--no-snapshot",
        action="store_true",
        help="brief on the two latest snapshots without taking a new one",
    )
    ap.add_argument("--fallback", action="store_true")
    ap.add_argument("--record", action="store_true")
    a = ap.parse_args()
    if a.fallback:
        return replay()
    return asyncio.run(run(a.no_snapshot, a.record))


if __name__ == "__main__":
    sys.exit(main())
