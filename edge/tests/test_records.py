"""Decision record writer, the issue statement, the policy envelope and the recon cross-check."""

from __future__ import annotations

import json

import pytest
from agents import Agent
from agents.items import ToolCallOutputItem

from edge import records
from edge.decide import Decision, Policy, enforce_policy, policy_action
from edge.recon import ReconReport, cross_check


def _record(**over) -> records.DecisionRecord:
    base = dict(
        record_id="edge-20260101T000000Z",
        zone_id="zone-test-0123",
        action="tighten",
        outcome="executed",
        previous_threshold=100,
        new_threshold=30,
        period=10,
        requests_per_second=7.5,
        elevated_rps=5.0,
        total_requests=13500,
        non_2xx_share=0.42,
        rationale="it's 7.5 rps against a 5 rps line",
        statement="REPLACE ...",
        rollback_statement="REPLACE ... 100",
        approved_by="operator",
        approval_method="terminal",
    )
    base.update(over)
    return records.DecisionRecord(**base)


def test_append_jsonl_appends_one_line_per_record(tmp_path):
    path = tmp_path / "decisions.jsonl"
    records.append_jsonl(_record(), path)
    records.append_jsonl(_record(record_id="edge-2"), path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["record_id"] == "edge-20260101T000000Z" and second["record_id"] == "edge-2"
    assert first["new_threshold"] == 30 and first["sink"] == "jsonl"
    assert first["timestamp"]


def test_issue_statement_is_escaped_and_carries_the_prefix():
    sql = records.render_issue_statement(_record(), "acme", "ops")
    assert sql.startswith("INSERT INTO github.issues.issues (owner, repo, title, body, labels)")
    assert "'acme', 'ops'" in sql
    assert "it''s 7.5 rps" in sql  # single quote doubled inside the literal
    assert "[agentic-demo] edge autopilot: tighten (executed) rate limit 100 -> 30 per 10s" in sql
    assert '["agentic-demo", "edge-autopilot"]' in sql


def test_split_repo_rejects_bad_specs():
    assert records.split_repo("acme/ops") == ("acme", "ops")
    for bad in ("acme", "acme/", "/ops", "a/b/c"):
        with pytest.raises(ValueError):
            records.split_repo(bad)


def test_policy_envelope_overrides_the_model():
    policy = Policy(elevated_rps=5.0, tightened_threshold=30, baseline_threshold=100, period=10)
    elevated = ReconReport(requests_per_second=7.5, threshold=100, period=10)
    quiet = ReconReport(requests_per_second=0.2, threshold=100, period=10)
    already = ReconReport(requests_per_second=9.0, threshold=30, period=10)
    assert policy_action(elevated, policy) == ("tighten", 30)
    assert policy_action(quiet, policy) == ("hold", 100)
    assert policy_action(already, policy) == ("hold", 30)
    # the model may not pick its own threshold, nor tighten on quiet traffic
    d = enforce_policy(Decision(action="tighten", new_threshold=1, rationale="r"), elevated, policy)
    assert (d.action, d.new_threshold) == ("tighten", 30) and "policy override" in d.rationale
    d = enforce_policy(Decision(action="tighten", new_threshold=30, rationale="r"), quiet, policy)
    assert (d.action, d.new_threshold) == ("hold", 100)
    d = enforce_policy(
        Decision(action="tighten", new_threshold=30, rationale="r"), elevated, policy
    )
    assert d.rationale == "r"


def _output_item(rows: list[dict]) -> ToolCallOutputItem:
    text = json.dumps({"rows": rows})
    return ToolCallOutputItem(
        agent=Agent(name="t"),
        raw_item={"type": "function_call_output", "call_id": "c", "output": text},
        output={"type": "text", "text": text},
    )


def test_cross_check_recomputes_from_tool_outputs():
    traffic = [
        {
            "datetime": "t1",
            "client_country_name": "AU",
            "edge_response_status": 200,
            "requests": 600,
            "bytes": 1,
        },
        {
            "datetime": "t1",
            "client_country_name": "US",
            "edge_response_status": 429,
            "requests": 300,
            "bytes": 1,
        },
        {
            "datetime": "t2",
            "client_country_name": "AU",
            "edge_response_status": 503,
            "requests": 100,
            "bytes": 1,
        },
    ]
    rule = [
        {
            "id": "rs1",
            "rule_id": "r1",
            "description": "agentic-demo rate limit",
            "threshold": 100,
            "period": 10,
        }
    ]
    model_said = ReconReport(total_requests=999, non_2xx_requests=0, threshold=0)
    report = cross_check(model_said, [_output_item(traffic), _output_item(rule)], 100)
    assert report.total_requests == 1000 and report.non_2xx_requests == 400
    assert report.non_2xx_share == pytest.approx(0.4)
    assert report.requests_per_second == pytest.approx(10.0)
    assert report.distinct_countries == 2 and report.top_countries == ["AU", "US"]
    assert (report.ruleset_id, report.rule_id, report.threshold, report.period) == (
        "rs1",
        "r1",
        100,
        10,
    )
    assert "code recomputed totals" in report.notes
