"""The decision record. Every run appends one to runs/edge-decisions.jsonl at the repo root; when
GITHUB_DECISIONS_REPO is set the record is filed as an issue instead, through the same approval
gate as the rate limit change (the INSERT is rendered from edge/queries/decision_issue.sql).

The original edgepilot published its decision to a Confluent Kafka topic
(edge-autopilot-decisions). StackQL has a confluent provider, so the same record could be written
to Kafka as a third sink; this demo does not build it."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .config import settings
from .queries import render_query, sql_literal


class DecisionRecord(BaseModel):
    record_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    zone_id: str
    action: str = Field(description="tighten | hold | restore")
    outcome: str = Field(description="executed | declined | no-change")
    previous_threshold: int
    new_threshold: int
    period: int
    window_since: str | None = None
    window_until: str | None = None
    total_requests: int | None = None
    requests_per_second: float | None = None
    elevated_rps: float | None = None
    non_2xx_share: float | None = None
    rationale: str = ""
    risk: str = ""
    statement: str | None = Field(default=None, description="the executed SQL, if any")
    rollback_statement: str | None = None
    approved_by: str | None = None
    approval_method: str | None = None
    trace_id: str | None = None
    sink: str = "jsonl"


def append_jsonl(record: DecisionRecord, path: Path | None = None) -> Path:
    target = path or settings().decisions_log
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record.model_dump(), default=str) + "\n")
    return target


def split_repo(spec: str) -> tuple[str, str]:
    if spec.count("/") != 1:
        raise ValueError("GITHUB_DECISIONS_REPO must be owner/repo")
    owner, repo = spec.split("/")
    if not owner or not repo:
        raise ValueError("GITHUB_DECISIONS_REPO must be owner/repo")
    return owner, repo


def issue_title(record: DecisionRecord) -> str:
    s = settings()
    return (
        f"[{s.demo_prefix}] edge autopilot: {record.action} ({record.outcome}) "
        f"rate limit {record.previous_threshold} -> {record.new_threshold} per {record.period}s"
    )


def issue_body(record: DecisionRecord) -> str:
    lines = [
        f"**Zone:** `{record.zone_id}`  ",
        f"**Action:** {record.action} ({record.outcome})  ",
        f"**Threshold:** {record.previous_threshold} -> {record.new_threshold} requests per {record.period}s  ",
    ]
    if record.requests_per_second is not None:
        lines.append(
            f"**Window:** {record.window_since} -> {record.window_until}, "
            f"{record.total_requests} requests, {record.requests_per_second:.3f} rps "
            f"(elevated line {record.elevated_rps} rps), non-2xx share "
            f"{(record.non_2xx_share or 0.0):.1%}  "
        )
    if record.approved_by:
        lines.append(f"**Approved by:** {record.approved_by} ({record.approval_method})  ")
    lines += ["", "## Rationale", "", record.rationale or "-", ""]
    if record.risk:
        lines += ["## Risk", "", record.risk, ""]
    if record.statement:
        lines += ["## Statement executed", "", "```sql", record.statement, "```", ""]
    if record.rollback_statement:
        lines += ["## Rollback", "", "```sql", record.rollback_statement, "```", ""]
    if record.trace_id:
        lines.append(
            f"Trace: https://platform.openai.com/traces/trace?trace_id={record.trace_id}  "
        )
    lines.append(
        "Filed by the edge autopilot. The rate limit change, if any, ran as one StackQL "
        "statement after explicit approval; this issue was filed through the same gate."
    )
    return "\n".join(lines)


def issue_params(record: DecisionRecord, owner: str, repo: str) -> dict[str, str]:
    """Parameters for edge/decision_issue, SQL-escaped; the gate renders the statement from them."""
    s = settings()
    return {
        "github_owner": owner,
        "github_repo": repo,
        "issue_title": sql_literal(issue_title(record)),
        "issue_body": sql_literal(issue_body(record)),
        "issue_labels": json.dumps([s.demo_prefix, "edge-autopilot"]),
    }


def render_issue_statement(record: DecisionRecord, owner: str, repo: str) -> str:
    return render_query("edge/decision_issue", **issue_params(record, owner, repo))
