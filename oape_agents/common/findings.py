"""Shared findings schema. Every sweep emits the same shape so the downstream artifacts
(GitHub issues, recertification report, briefs) and the escalation step are generic."""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, Field

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]


class Severity(StrEnum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"

    def at_least(self, other: str) -> bool:
        return SEVERITY_ORDER.index(self.value) >= SEVERITY_ORDER.index(other)


class Finding(BaseModel):
    """One finding. `evidence` is the query id and the rows that support it, rendered as text,
    so the finding is reviewable without re-running anything."""

    provider: str = Field(
        description="aws | azure | google | github | entra_id | okta | openai_admin"
    )
    resource: str = Field(description="Stable identifier of the affected resource (ARN, id, name)")
    finding_type: str = Field(description="snake_case check id, e.g. s3_bucket_public")
    severity: Severity
    title: str = Field(description="One line, matter of fact")
    evidence: str = Field(description="Query id and the supporting rows/values, as text")
    proposed_remediation: str = Field(
        description="The exact StackQL statement(s) or configuration change that would fix it"
    )
    query_id: str = Field(default="", description="queries/<path> the evidence came from")
    monthly_cost_estimate_usd: float | None = Field(
        default=None, description="FinOps only: estimated monthly waste in USD, if derivable"
    )

    @property
    def fingerprint(self) -> str:
        raw = f"{self.provider}|{self.finding_type}|{self.resource}".lower()
        return hashlib.sha1(raw.encode()).hexdigest()[:10]


class FindingSet(BaseModel):
    scenario: str
    findings: list[Finding]
    summary: str = Field(description="Two to four sentences for the console brief")


class Assessment(BaseModel):
    """Frontier-tier judgement on an escalated finding."""

    finding_fingerprint: str
    material: bool = Field(description="True if this warrants action now")
    rationale: str = Field(description="Why, correlating with other findings where relevant")
    correlated_fingerprints: list[str] = Field(default_factory=list)
    remediation_plan: str = Field(description="Ordered steps a platform engineer would take")
    remediation_sql: str = Field(
        default="", description="StackQL mutation statement(s) proposed, for human review only"
    )
    blast_radius: str = Field(description="What else the remediation touches")


class AssessmentSet(BaseModel):
    assessments: list[Assessment]
    executive_summary: str


def escalation_candidates(fs: FindingSet, threshold: str) -> list[Finding]:
    return [f for f in fs.findings if f.severity.at_least(threshold)]
