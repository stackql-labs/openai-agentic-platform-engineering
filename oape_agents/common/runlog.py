"""Console brief and fallback replay. Every scenario can run `--fallback` to replay a recorded
run from fallbacks/<scenario>.json without touching a live API (make rehearse)."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .config import FALLBACKS_DIR
from .findings import SEVERITY_ORDER, Finding, FindingSet

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
console = Console(width=None if sys.stdout.isatty() else 140)


def banner(title: str, subtitle: str = "") -> None:
    console.rule(f"[bold]{title}[/bold]")
    if subtitle:
        console.print(subtitle)


def _sev_rank(f: Finding) -> int:
    return SEVERITY_ORDER.index(f.severity.value)


def print_findings(fs: FindingSet) -> None:
    if not fs.findings:
        console.print(Panel("No findings. Estate is clean for this sweep.", title=fs.scenario))
        return
    t = Table(title=f"{fs.scenario}: {len(fs.findings)} findings")
    for c in ("sev", "provider", "finding", "resource", "est. USD/mo"):
        t.add_column(c)
    for f in sorted(fs.findings, key=lambda x: -_sev_rank(x)):
        cost = ""
        if f.monthly_cost_estimate_usd is not None:
            cost = f"{f.monthly_cost_estimate_usd:,.2f}"
        t.add_row(f.severity.value, f.provider, f.finding_type, f.resource[:70], cost)
    console.print(t)
    console.print(Panel(fs.summary, title="brief"))


def fallback_path(scenario: str) -> Path:
    return FALLBACKS_DIR / f"{scenario}.json"


def load_fallback(scenario: str) -> dict:
    p = fallback_path(scenario)
    if not p.exists():
        raise FileNotFoundError(f"no fallback recorded for {scenario}: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def save_fallback(scenario: str, payload: dict) -> Path:
    FALLBACKS_DIR.mkdir(parents=True, exist_ok=True)
    p = fallback_path(scenario)
    payload = {"recorded_at": datetime.now(UTC).isoformat(), **payload}
    p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return p
