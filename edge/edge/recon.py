"""Recon agent (mini tier, read-only server). Runs the two SELECTs from edge/queries/ verbatim
and returns a structured report. The arithmetic (totals, shares, requests per second) is
recomputed by code from the rows the model saw, so the decision never rests on model arithmetic."""

from __future__ import annotations

from typing import Any

from agents import Runner
from agents.items import ToolCallOutputItem
from pydantic import BaseModel, Field

from .costs import RunLedger
from .mcp import StackQLServer, rows_from_output
from .queries import render_query
from .tiers import make_agent, tier

RECON_INSTRUCTIONS = """You are the recon agent for one Cloudflare zone.

You are given exactly two SELECT statements. Run each one once with run_select_query (pass
format "json"), verbatim - do not rewrite, reorder, filter or compose any other SQL, and do not
call any other tool. Then fill in the report from the rows you got back:

- total_requests: sum of the `requests` column across all rows of statement 1
- distinct_countries: number of distinct client_country_name values
- non_2xx_requests: sum of `requests` for rows whose edge_response_status is outside 200-299
- non_2xx_share: non_2xx_requests / total_requests (0 when there are no requests)
- requests_per_second: total_requests / window_seconds
- top_countries: up to five country names by requests, highest first
- ruleset_id, rule_id, threshold, period: from the single row of statement 2
- notes: one or two sentences: anything unusual (a single status code dominating, one country
  dominating, an empty window). Matter of fact, no adjectives.

If a statement returns no rows, report zeros (or empty strings) and say so in notes."""


class ReconReport(BaseModel):
    total_requests: int = 0
    distinct_countries: int = 0
    non_2xx_requests: int = 0
    non_2xx_share: float = Field(default=0.0, description="0..1")
    requests_per_second: float = 0.0
    top_countries: list[str] = Field(default_factory=list)
    ruleset_id: str = ""
    rule_id: str = ""
    threshold: int = Field(default=0, description="current requests_per_period of the rule")
    period: int = Field(default=0, description="seconds")
    notes: str = ""


def _int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


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
            threshold=_int(rule.get("threshold")) or report.threshold,
            period=_int(rule.get("period")) or report.period,
        )
    if notes:
        update["notes"] = (report.notes + " " + "; ".join(notes)).strip()
    return report.model_copy(update=update)


async def run_recon(
    server: StackQLServer, since: str, until: str, window_seconds: int, ledger: RunLedger
) -> ReconReport:
    traffic_sql = render_query("edge/zone_traffic", since=since, until=until)
    ruleset_sql = render_query("edge/rate_limit_ruleset")
    agent = make_agent(
        "sweep",
        name="edge-recon",
        instructions=RECON_INSTRUCTIONS,
        mcp_servers=[server],
        output_type=ReconReport,
    )
    prompt = (
        f"window_seconds: {window_seconds} (since {since}, until {until})\n\n"
        f"Statement 1 (edge/zone_traffic):\n{traffic_sql}\n\n"
        f"Statement 2 (edge/rate_limit_ruleset):\n{ruleset_sql}\n"
    )
    result = await Runner.run(agent, prompt, max_turns=6)
    ledger.record("recon", tier("sweep").model, result)
    report = result.final_output_as(ReconReport)
    return cross_check(report, list(result.new_items), window_seconds)
