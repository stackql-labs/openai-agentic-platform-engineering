"""Recon agent (mini tier, read-only server). It gets an intent (edge/prompts/recon.md): traffic to
the demo zone in the window and the rate limit rule currently applied. It finds the resources
itself through the query library and the discovery tools, and returns a structured report. The
arithmetic (totals, shares, requests per second) is recomputed by code from the rows the model saw,
so the decision never rests on model arithmetic, and the statements it ran are collected from the
tool calls, not from what it says it ran."""

from __future__ import annotations

import json
import re
from typing import Any

from agents import Runner
from agents.items import ToolCallItem, ToolCallOutputItem
from pydantic import BaseModel, Field

from .costs import RunLedger, tool_name_of
from .mcp import StackQLServer, rows_from_output
from .tiers import make_agent, tier

_FROM_RE = re.compile(r"\bFROM\s+([a-z0-9_]+\.[a-z0-9_]+\.[a-z0-9_]+)", re.IGNORECASE)
RECON_MAX_TURNS = 14


class ReconReport(BaseModel):
    total_requests: int = 0
    distinct_countries: int = 0
    non_2xx_requests: int = 0
    non_2xx_share: float = Field(default=0.0, description="0..1")
    requests_per_second: float = 0.0
    top_countries: list[str] = Field(default_factory=list)
    ruleset_id: str = ""
    rule_id: str = ""
    rule_description: str = ""
    threshold: int = Field(default=0, description="current requests_per_period of the rule")
    period: int = Field(default=0, description="seconds")
    rules_json: str = Field(default="", description="the current rules array as returned, JSON")
    notes: str = ""
    statements: list[str] = Field(
        default_factory=list, description="filled by code from the tool calls: the SELECTs run"
    )
    resources_read: list[str] = Field(
        default_factory=list, description="filled by code: provider.service.resource per statement"
    )


def _int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _first_rule(rules: Any) -> dict[str, Any] | None:
    if isinstance(rules, str):
        try:
            rules = json.loads(rules)
        except json.JSONDecodeError:
            return None
    if isinstance(rules, list) and rules and isinstance(rules[0], dict):
        return rules[0]
    return None


def statements_run(items: list[Any]) -> list[str]:
    """The SQL of every run_select_query call in the run, in order, from the tool call items."""
    out: list[str] = []
    for item in items:
        if not isinstance(item, ToolCallItem) or tool_name_of(item) != "run_select_query":
            continue
        raw = item.raw_item
        args = getattr(raw, "arguments", None)
        if args is None and isinstance(raw, dict):
            args = raw.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        sql = (args or {}).get("sql") if isinstance(args, dict) else None
        if sql:
            out.append(str(sql).strip())
    return out


def cross_check(report: ReconReport, items: list[Any], window_seconds: int) -> ReconReport:
    """Recompute the numbers from the tool outputs in the run. Code has the last word."""
    traffic: list[dict] = []
    rule: dict | None = None
    for item in items:
        if not isinstance(item, ToolCallOutputItem):
            continue
        for row in rows_from_output(item.output):
            if "requests" in row and "edge_response_status" in row:
                traffic.append(row)
            elif "threshold" in row and "rule_id" in row:
                rule = row
            elif "rules" in row:
                first = _first_rule(row.get("rules"))
                if first is not None:
                    rl = first.get("ratelimit") or {}
                    rule = {
                        "id": row.get("id"),
                        "rule_id": first.get("id"),
                        "description": first.get("description"),
                        "threshold": rl.get("requests_per_period"),
                        "period": rl.get("period"),
                        "rules": row.get("rules"),
                    }
    update: dict[str, Any] = {}
    notes: list[str] = []
    if traffic:
        total = sum(_int(r.get("requests")) for r in traffic)
        countries = {str(r.get("client_country_name") or "") for r in traffic} - {""}
        non_2xx = sum(
            _int(r.get("requests"))
            for r in traffic
            if not 200 <= _int(r.get("edge_response_status")) <= 299
        )
        by_country: dict[str, int] = {}
        for r in traffic:
            c = str(r.get("client_country_name") or "")
            by_country[c] = by_country.get(c, 0) + _int(r.get("requests"))
        top = [c for c, _ in sorted(by_country.items(), key=lambda kv: -kv[1]) if c][:5]
        update.update(
            total_requests=total,
            distinct_countries=len(countries),
            non_2xx_requests=non_2xx,
            non_2xx_share=(non_2xx / total) if total else 0.0,
            requests_per_second=(total / window_seconds) if window_seconds else 0.0,
            top_countries=top,
        )
        if total != report.total_requests or non_2xx != report.non_2xx_requests:
            notes.append(
                f"code recomputed totals from {len(traffic)} rows "
                f"(model reported {report.total_requests} total, {report.non_2xx_requests} non-2xx)"
            )
    else:
        update["requests_per_second"] = (
            (report.total_requests / window_seconds) if window_seconds else 0.0
        )
    if rule:
        update.update(
            ruleset_id=str(rule.get("id") or report.ruleset_id),
            rule_id=str(rule.get("rule_id") or report.rule_id),
            rule_description=str(rule.get("description") or report.rule_description),
            threshold=_int(rule.get("threshold")) or report.threshold,
            period=_int(rule.get("period")) or report.period,
        )
        raw_rules = rule.get("rules")
        if raw_rules:
            update["rules_json"] = (
                raw_rules if isinstance(raw_rules, str) else json.dumps(raw_rules)
            )
    statements = statements_run(items)
    if statements:
        update["statements"] = statements
        seen: list[str] = []
        for sql in statements:
            for name in _FROM_RE.findall(sql):
                if name.lower() not in seen:
                    seen.append(name.lower())
        update["resources_read"] = seen
    if notes:
        update["notes"] = (report.notes + " " + "; ".join(notes)).strip()
    return report.model_copy(update=update)


async def run_recon(
    server: StackQLServer, instructions: str, window_seconds: int, ledger: RunLedger
) -> ReconReport:
    """The intent is in the instructions (rendered from edge/prompts/recon.md); the input only
    tells the agent to start."""
    agent = make_agent(
        "sweep",
        name="edge-recon",
        instructions=instructions,
        mcp_servers=[server],
        output_type=ReconReport,
    )
    result = await Runner.run(agent, "Begin the recon now.", max_turns=RECON_MAX_TURNS)
    ledger.record("recon", tier("sweep").model, result)
    report = result.final_output_as(ReconReport)
    return cross_check(report, list(result.new_items), window_seconds)
