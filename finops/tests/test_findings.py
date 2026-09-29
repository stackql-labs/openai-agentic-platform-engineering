"""Findings schema: the shared shape validates, severities follow the cost rule, plans sort by
savings, and both models' output types produce a JSON schema for structured outputs."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from finops.findings import (
    DeferredItem,
    Finding,
    FindingSet,
    ProposedDeletion,
    ProviderPlan,
    Severity,
    severity_for_cost,
)


def _finding(**over) -> Finding:
    base = dict(
        provider="aws",
        resource="vol-0123",
        finding_type="unattached_volume",
        severity="low",
        title="EBS volume vol-0123 is unattached (estimated 0.10 USD/month)",
        evidence="finops/aws_unattached_volumes: vol-0123, 1 GB gp3, est_monthly_usd 0.10",
        proposed_remediation="DELETE FROM aws.ec2.volumes WHERE volume_id = 'vol-0123' AND region = 'ap-southeast-2'",
        query_id="finops/aws_unattached_volumes",
        monthly_cost_estimate_usd=0.10,
    )
    base.update(over)
    return Finding(**base)


def test_finding_shape_and_fingerprint():
    f = _finding()
    assert set(f.model_dump()) == {
        "provider",
        "resource",
        "finding_type",
        "severity",
        "title",
        "evidence",
        "proposed_remediation",
        "query_id",
        "monthly_cost_estimate_usd",
    }
    assert f.fingerprint == _finding(title="other").fingerprint
    assert f.fingerprint != _finding(resource="vol-9").fingerprint
    with pytest.raises(ValidationError):
        _finding(severity="urgent")


def test_severity_rule():
    assert severity_for_cost(None) == Severity.info
    assert severity_for_cost(0.1) == Severity.low
    assert severity_for_cost(5) == Severity.medium
    assert severity_for_cost(50) == Severity.high
    assert Severity.high.at_least("medium") and not Severity.low.at_least("medium")


def test_finding_set_totals_and_grouping():
    fs = FindingSet(
        findings=[
            _finding(),
            _finding(provider="azure", resource="d1", monthly_cost_estimate_usd=3.65),
            _finding(provider="azure", resource="d2", monthly_cost_estimate_usd=None),
        ],
        summary="three findings",
    )
    assert fs.total_monthly_usd() == 3.75
    assert {k: len(v) for k, v in fs.by_provider().items()} == {"aws": 1, "azure": 2}


def test_provider_plan_orders_by_savings():
    plan = ProviderPlan(
        provider="aws",
        rationale="both idle and tagged",
        proposed=[
            ProposedDeletion(
                resource="vol-1",
                finding_type="unattached_volume",
                monthly_cost_estimate_usd=0.1,
                statement="DELETE ...",
                reason="idle",
            ),
            ProposedDeletion(
                resource="eipalloc-1",
                finding_type="unassociated_ip",
                monthly_cost_estimate_usd=3.65,
                statement="EXEC ...",
                reason="idle",
            ),
        ],
        deferred=[DeferredItem(resource="snap-1", reason="only copy")],
    )
    assert [p.resource for p in plan.sorted_proposed()] == ["eipalloc-1", "vol-1"]
    assert plan.proposed_monthly_usd() == 3.75


def test_output_types_have_json_schemas():
    for model in (FindingSet, ProviderPlan):
        schema = model.model_json_schema()
        assert schema["type"] == "object" and "properties" in schema
