"""Sweep plumbing that needs no model: provider selection, the tenancy block and trigger the
prompts receive, severity normalisation, the cost ledger and the markdown cost report."""

from __future__ import annotations

import json

import pytest

from finops.costs import RunEntry, RunLedger, price_for
from finops.findings import Finding, FindingSet, ProposedDeletion, ProviderPlan
from finops.report import write_markdown_report
from finops.sweep import normalise, select_providers, sweep_trigger, tenancy_block


def _f(provider, resource, cost, ftype="unattached_volume"):
    return Finding(
        provider=provider,
        resource=resource,
        finding_type=ftype,
        severity="info",
        title=f"{resource} idle",
        evidence="finops/x: row",
        proposed_remediation="DELETE ...",
        query_id=f"finops/{provider}_x",
        monthly_cost_estimate_usd=cost,
    )


def test_select_providers_uses_credentials_or_flag(monkeypatch):
    for v in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "GOOGLE_CREDENTIALS"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("AZURE_TENANT_ID", "t")
    monkeypatch.setenv("AZURE_CLIENT_ID", "c")
    monkeypatch.setenv("AZURE_CLIENT_SECRET", "s")
    assert select_providers(None) == ["azure"]
    assert select_providers(["google", "aws", "google"]) == ["google", "aws"]
    with pytest.raises(SystemExit, match="unknown provider"):
        select_providers(["oracle"])
    monkeypatch.setenv("GOOGLE_PROJECT", "")
    with pytest.raises(SystemExit, match="GOOGLE_PROJECT"):
        select_providers(["google"])


def test_tenancy_block_and_trigger_carry_no_sql():
    block = tenancy_block(["aws", "google"])
    assert block == "- aws: region ap-southeast-2\n- google: project demo-project"
    assert "azure" not in block
    trig = sweep_trigger(["aws"])
    assert "Providers in scope: aws." in trig and "SELECT" not in trig.upper()


def test_normalise_reapplies_severity_and_scope():
    fs = FindingSet(
        findings=[_f("aws", "a", 60), _f("azure", "b", None), _f("oracle", "c", 1)],
        summary="s",
    )
    out = normalise(fs)
    assert [f.provider for f in out.findings] == ["aws", "azure"]
    assert out.findings[0].severity == "high" and out.findings[1].severity == "info"


def test_pricing_and_ledger(monkeypatch):
    monkeypatch.setenv(
        "OPENAI_PRICING_JSON",
        json.dumps({"test-model": {"input": 1, "cached_input": 0.1, "output": 10}}),
    )
    assert price_for("test-model-2026-01-01")["output"] == 10
    assert price_for("unknown-model") is None
    e = RunEntry(
        "step", "test-model", input_tokens=1_000_000, cached_tokens=500_000, output_tokens=100_000
    )
    assert e.cost_usd == pytest.approx(0.5 + 0.05 + 1.0)
    ledger = RunLedger("finops", trace_id="trace_abc")
    ledger.entries.append(e)
    d = ledger.to_dict()
    assert d["trace_url"].endswith("trace_id=trace_abc") and d["total_cost_usd"] == 1.55


def test_markdown_report(tmp_path):
    fs = FindingSet(
        findings=[
            _f("aws", "vol-1", 0.1),
            _f("aws", "eipalloc-1", 3.65, "unassociated_ip"),
            _f("google", "disk-1", 0.4),
        ],
        summary="Three idle resources.",
    )
    plan = ProviderPlan(
        provider="aws",
        rationale="Both carry the demo tag.",
        proposed=[
            ProposedDeletion(
                resource="vol-1",
                finding_type="unattached_volume",
                monthly_cost_estimate_usd=0.1,
                statement="DELETE FROM aws.ec2.volumes WHERE volume_id = 'vol-1' AND region = 'r'",
                reason="idle",
            ),
            ProposedDeletion(
                resource="eipalloc-1",
                finding_type="unassociated_ip",
                monthly_cost_estimate_usd=3.65,
                statement="EXEC aws.ec2.address.release_address @region = 'r', @AllocationId = 'eipalloc-1'",
                reason="idle",
            ),
        ],
        deferred=[],
    )
    ledger = RunLedger("finops", trace_id="trace_x")
    ledger.entries.append(
        RunEntry("sweep (classify)", "m", requests=2, select_calls=3, tool_calls=3)
    )
    path = write_markdown_report(fs, [plan], ledger, ["aws", "azure", "google"], out_dir=tmp_path)
    text = path.read_text()
    assert path.name == f"finops-{ledger.stamp}.md"
    assert "| aws | 2 | 3.75 | 2 | 3.75 | 0 |" in text
    assert "| total | 3 | 4.15 | 2 | 3.75 | 0 |" in text
    assert "## azure\n\nNo findings." in text
    assert "Reasoning tier not run" in text  # google had findings but no plan
    assert text.index("eipalloc-1'") < text.index("volume_id = 'vol-1'")  # ordered by savings
    assert "trace_id=trace_x" in text and "not executed" in text
    assert "mutation" in text and "| sweep (classify) | m | 2 |" in text
    assert "query `finops/aws_x`" in text and "queries/" not in text.split("## Evidence")[1]
