"""The query loader: headers parse, placeholders render from env and overrides, missing values
name the variable, and no agent module carries inline provider SQL."""

from __future__ import annotations

import re

import pytest

from edge import queries
from edge.config import USECASE_DIR

EXPECTED = {
    "edge/zone_traffic": "cloudflare",
    "edge/rate_limit_ruleset": "cloudflare",
    "edge/tighten_rate_limit": "cloudflare",
    "edge/decision_repo": "github",
    "edge/decision_issue": "github",
}


def test_every_query_has_a_complete_header():
    qs = {q.id: q for q in queries.list_queries()}
    assert set(qs) == set(EXPECTED)
    for qid, provider in EXPECTED.items():
        q = qs[qid]
        assert q.providers == [provider]
        assert q.description and q.params and q.expected_columns
        assert "{{" not in q.description
    selects = {q.id for q in qs.values() if q.is_select}
    assert selects == {"edge/zone_traffic", "edge/rate_limit_ruleset", "edge/decision_repo"}


def test_render_substitutes_env_and_overrides():
    sql = queries.render_query(
        "edge/zone_traffic", since="2026-01-01T00:00:00Z", until="2026-01-01T00:30:00Z"
    )
    assert "zone_tag = 'zone-test-0123'" in sql
    assert "since = '2026-01-01T00:00:00Z'" in sql and "until = '2026-01-01T00:30:00Z'" in sql
    assert "{{" not in sql
    assert sql.startswith("SELECT")


def test_override_beats_env():
    sql = queries.render_query("edge/rate_limit_ruleset", cloudflare_zone_id="other-zone")
    assert "zone_id = 'other-zone'" in sql


def test_missing_value_names_the_variable(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ZONE_ID")
    with pytest.raises(ValueError, match="CLOUDFLARE_ZONE_ID"):
        queries.render_query("edge/rate_limit_ruleset")


def test_tighten_template_renders_threshold_and_prefix():
    sql = queries.render_query("edge/tighten_rate_limit", threshold=30, demo_prefix="agentic-demo")
    assert sql.startswith("REPLACE cloudflare.rulesets.phases SET rules = ")
    assert '"requests_per_period":30' in sql
    assert '"description":"agentic-demo rate limit (managed by stackql-deploy)"' in sql
    assert "zone_id = 'zone-test-0123' AND ruleset_phase = 'http_ratelimit'" in sql


def test_unknown_query_id():
    with pytest.raises(FileNotFoundError):
        queries.load_query("edge/nope")


def test_no_inline_provider_sql_in_agent_code():
    pattern = re.compile(r"\b(FROM|INTO|REPLACE)\s+(cloudflare|github)\.\w+\.\w+")
    offenders = [
        p.name
        for p in (USECASE_DIR / "edge").glob("*.py")
        if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders
