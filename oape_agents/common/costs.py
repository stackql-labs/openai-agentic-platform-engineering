"""Run cost and trace summary. Every scenario ends by printing this, so the consumption
economics stay in frame: requests, tokens (cached / reasoning broken out), USD by model tier,
tool calls (SELECT vs mutation), the OpenAI trace URL and the MCP audit log location."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml
from agents import RunResult, Usage
from agents.items import ToolCallItem
from rich.console import Console
from rich.table import Table

from .config import CONFIG_DIR, RUNS_DIR, settings

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
console = Console(width=None if sys.stdout.isatty() else 140)


def _pricing() -> dict[str, dict[str, float]]:
    table = yaml.safe_load((CONFIG_DIR / "pricing.yaml").read_text()) or {}
    override = os.environ.get("OPENAI_PRICING_JSON", "")
    if override:
        table.update(json.loads(override))
    return table


def price_for(model: str) -> dict[str, float] | None:
    table = _pricing()
    if model in table:
        return table[model]
    # dated snapshots like gpt-5.4-mini-2026-03-17 -> gpt-5.4-mini
    parts = model.split("-")
    if len(parts) >= 4 and parts[-3].isdigit() and parts[-2].isdigit() and parts[-1].isdigit():
        return table.get("-".join(parts[:-3]))
    return None


def tool_name_of(item: ToolCallItem) -> str:
    raw = item.raw_item
    name = getattr(raw, "name", None)
    if name:
        return str(name)
    if isinstance(raw, dict):
        return str(raw.get("name", "?"))
    return "?"


@dataclass
class RunEntry:
    label: str
    model: str
    requests: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    tool_calls: int = 0
    select_calls: int = 0
    mutation_calls: int = 0
    tool_names: dict[str, int] = field(default_factory=dict)

    @property
    def cost_usd(self) -> float | None:
        p = price_for(self.model)
        if not p:
            return None
        uncached = max(self.input_tokens - self.cached_tokens, 0)
        cached_rate = p.get("cached_input", p["input"])
        return (
            uncached * p["input"]
            + self.cached_tokens * cached_rate
            + self.output_tokens * p["output"]
        ) / 1_000_000


def _entry_from_usage(label: str, model: str, u: Usage) -> RunEntry:
    return RunEntry(
        label=label,
        model=model,
        requests=u.requests,
        input_tokens=u.input_tokens,
        cached_tokens=getattr(u.input_tokens_details, "cached_tokens", 0) or 0,
        output_tokens=u.output_tokens,
        reasoning_tokens=getattr(u.output_tokens_details, "reasoning_tokens", 0) or 0,
    )


@dataclass
class RunLedger:
    scenario: str
    trace_id: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    entries: list[RunEntry] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def record(self, label: str, model: str, result: RunResult) -> RunEntry:
        e = _entry_from_usage(label, model, result.context_wrapper.usage)
        for item in result.new_items:
            if isinstance(item, ToolCallItem):
                name = tool_name_of(item)
                e.tool_calls += 1
                e.tool_names[name] = e.tool_names.get(name, 0) + 1
                if name == "run_select_query":
                    e.select_calls += 1
                if name in ("run_mutation_query", "run_lifecycle_operation"):
                    e.mutation_calls += 1
        self.entries.append(e)
        return e

    def record_usage(self, label: str, model: str, usage: Usage) -> None:
        self.entries.append(_entry_from_usage(label, model, usage))

    def record_dict(self, d: dict) -> None:
        """Replay an entry from a recorded run (fallbacks)."""
        keys = {f for f in RunEntry.__dataclass_fields__}
        self.entries.append(RunEntry(**{k: v for k, v in d.items() if k in keys}))

    @property
    def total_cost_usd(self) -> float:
        return sum(e.cost_usd or 0.0 for e in self.entries)

    @property
    def trace_url(self) -> str | None:
        if not self.trace_id:
            return None
        return f"https://platform.openai.com/traces/trace?trace_id={self.trace_id}"

    def to_dict(self) -> dict:
        return {
            "scenario": self.scenario,
            "trace_id": self.trace_id,
            "trace_url": self.trace_url,
            "started_at": self.started_at.isoformat(),
            "duration_s": (datetime.now(UTC) - self.started_at).total_seconds(),
            "total_cost_usd": round(self.total_cost_usd, 6),
            "entries": [{**e.__dict__, "cost_usd": e.cost_usd} for e in self.entries],
            "notes": self.notes,
        }

    def print_summary(self, replay: bool = False) -> None:
        s = settings()
        dur = (datetime.now(UTC) - self.started_at).total_seconds()
        title = f"run cost and trace - {self.scenario}" + (" (replayed)" if replay else "")
        t = Table(title=title, show_lines=False)
        cols = (
            "step",
            "model",
            "req",
            "input",
            "cached",
            "output",
            "reasoning",
            "tool calls",
            "USD",
        )
        for col in cols:
            t.add_column(col, justify="left" if col in ("step", "model") else "right")
        for e in self.entries:
            cost = f"{e.cost_usd:.4f}" if e.cost_usd is not None else "n/a"
            tools = f"{e.tool_calls} ({e.select_calls} select, {e.mutation_calls} mutation)"
            t.add_row(
                e.label,
                e.model,
                str(e.requests),
                f"{e.input_tokens:,}",
                f"{e.cached_tokens:,}",
                f"{e.output_tokens:,}",
                f"{e.reasoning_tokens:,}",
                tools,
                cost,
            )
        tot_tools = sum(e.tool_calls for e in self.entries)
        tot_sel = sum(e.select_calls for e in self.entries)
        tot_mut = sum(e.mutation_calls for e in self.entries)
        t.add_row(
            "total",
            "",
            str(sum(e.requests for e in self.entries)),
            f"{sum(e.input_tokens for e in self.entries):,}",
            f"{sum(e.cached_tokens for e in self.entries):,}",
            f"{sum(e.output_tokens for e in self.entries):,}",
            f"{sum(e.reasoning_tokens for e in self.entries):,}",
            f"{tot_tools} ({tot_sel} select, {tot_mut} mutation)",
            f"{self.total_cost_usd:.4f}",
            style="bold",
        )
        console.print(t)
        console.print(
            f"duration {dur:.0f}s  |  tiers: sweep={s.sweep.model} reasoning={s.reasoning.model}"
        )
        unknown = sorted(
            {
                e.model
                for e in self.entries
                if e.model not in ("-", "") and price_for(e.model) is None
            }
        )
        if unknown:
            console.print(f"[yellow]no pricing for {unknown} - add to config/pricing.yaml[/yellow]")
        if self.trace_url:
            console.print(f"trace: {self.trace_url}")
        console.print(f"stackql mcp audit log: {s.mcp_audit_log}")
        for n in self.notes:
            console.print(f"note: {n}")

    def save(self, extra: dict | None = None) -> Path:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        ts = self.started_at.strftime("%Y%m%dT%H%M%SZ")
        path = RUNS_DIR / f"{self.scenario}-{ts}.json"
        payload = self.to_dict()
        if extra:
            payload.update(extra)
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return path
