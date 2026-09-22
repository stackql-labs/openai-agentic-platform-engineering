from oape_agents.common.findings import Finding, FindingSet, Severity, escalation_candidates


def _f(sev: str, res: str = "r1") -> Finding:
    return Finding(
        provider="aws",
        resource=res,
        finding_type="x",
        severity=Severity(sev),
        title="t",
        evidence="e",
        proposed_remediation="p",
    )


def test_severity_order():
    assert Severity.high.at_least("medium")
    assert not Severity.low.at_least("medium")
    assert Severity.critical.at_least("critical")


def test_fingerprint_is_stable_and_case_insensitive():
    a = _f("high", "ARN:x")
    b = _f("low", "arn:x")
    assert a.fingerprint == b.fingerprint


def test_escalation_candidates():
    fs = FindingSet(
        scenario="cspm", findings=[_f("low"), _f("high", "r2"), _f("critical", "r3")], summary="s"
    )
    assert [f.resource for f in escalation_candidates(fs, "high")] == ["r2", "r3"]
