"""Shared findings shape (every use case in this repo emits it) and the FinOps-specific
per-provider remediation plan the reasoning tier returns. Both are structured outputs."""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, Field

SEVERITY_ORDER = ["info", "low", "medium", "high", "critical"]
FINDING_TYPES = ("unattached_volume", "unassociated_ip", "stale_snapshot")


class Severity(StrEnum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"

    def at_least(self, other: str) -> bool:
        return SEVERITY_ORDER.index(self.value) >= SEVERITY_ORDER.index(other)


def severity_for_cost(monthly_usd: float | None) -> Severity:
    """The deterministic rule the sweep tier is told to apply; code re-applies it."""
    if monthly_usd is None:
        return Severity.info
    if monthly_usd >= 50:
        return Severity.high
    if monthly_usd >= 5:
        return Severity.medium
    return Severity.low


class Finding(BaseModel):
    """One finding. `evidence` is the query id (a query library id, or the
    provider.service.resource the model selected from) and the row values that support it, as
    text, so the finding is reviewable without re-running anything."""

    provider: str = Field(description="aws | azure | google")
    resource: str = Field(description="Stable identifier of the resource (id, name or ARN)")
    finding_type: str = Field(description="unattached_volume | unassociated_ip | stale_snapshot")
    severity: Severity
    title: str = Field(description="One line, matter of fact")
    evidence: str = Field(description="Query id and the supporting row values, as text")
    proposed_remediation: str = Field(
        description="The action that would remove the resource (delete the volume, release the address, ...) - never executed by this program"
    )
    query_id: str = Field(
        default="",
        description="The query library id used, or the provider.service.resource selected from",
    )
    monthly_cost_estimate_usd: float | None = Field(
        default=None,
        description="List-price monthly estimate in USD for this resource, computed by the model from the row's size and type; an estimate, not billing data",
    )

    @property
    def fingerprint(self) -> str:
        raw = f"{self.provider}|{self.finding_type}|{self.resource}".lower()
        return hashlib.sha1(raw.encode()).hexdigest()[:10]


class FindingSet(BaseModel):
    findings: list[Finding]
    summary: str = Field(description="Two to four sentences for the console brief")

    def by_provider(self) -> dict[str, list[Finding]]:
        out: dict[str, list[Finding]] = {}
        for f in self.findings:
            out.setdefault(f.provider, []).append(f)
        return out

    def total_monthly_usd(self) -> float:
        return round(sum(f.monthly_cost_estimate_usd or 0.0 for f in self.findings), 2)


class ProposedDeletion(BaseModel):
    """One statement the reasoning tier judges safe to run now. For human review only."""

    resource: str
    finding_type: str
    monthly_cost_estimate_usd: float = Field(default=0.0)
    statement: str = Field(
        description="One StackQL DELETE or EXEC statement, complete and literal, drafted from the contract describe_method reported"
    )
    reason: str = Field(
        description="One sentence: why this is safe to delete now and which method the statement was drafted from"
    )


class DeferredItem(BaseModel):
    resource: str
    reason: str = Field(description="One sentence: why this is not proposed for deletion now")


class ProviderPlan(BaseModel):
    """The reasoning tier's verdict for one provider: what is safe to delete now, in savings
    order, plus what it deferred and why."""

    provider: str
    rationale: str = Field(description="Two to four sentences on how the batch was judged")
    proposed: list[ProposedDeletion]
    deferred: list[DeferredItem]

    def sorted_proposed(self) -> list[ProposedDeletion]:
        return sorted(self.proposed, key=lambda p: -p.monthly_cost_estimate_usd)

    def proposed_monthly_usd(self) -> float:
        return round(sum(p.monthly_cost_estimate_usd for p in self.proposed), 2)
