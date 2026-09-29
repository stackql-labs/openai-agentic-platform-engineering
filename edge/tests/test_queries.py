"""The query loader over the reduced set: two examples the prompts may cite, and the code-owned
statements the model never sees (the gate's target assertion, the restore statement, the decision
repo assertion and the decision issue INSERT). Headers parse, placeholders render from env and
overrides, missing values name the variable, and no agent module carries inline provider SQL."""

from __future__ import annotations

import re

import pytest

from edge import queries
from edge.config import USECASE_DIR

EXAMPLES = {
    "edge/examples/zone_traffic": "cloudflare",
    "edge/examples/rate_limit_ruleset": "cloudflare",
}
CODE_OWNED = {
    "edge/assert_demo_rule": "cloudflare",
    "edge/restore_rate_limit": "cloudflare",
    "edge/decision_repo": "github",
    "edge/decision_issue": "github",
}


def test_every_query_has_a_complete_header():
    qs = {q.id: q for q in queries.list_queries()}
    assert set(qs) == set(EXAMPLES) | set(CODE_OWNED)
    for qid, provider in {**EXAMPLES, **CODE_OWNED}.items():
        q = qs[qid]
        assert q.providers == [provider]
        assert q.description and q.params and q.expected_columns
        assert "{{" not in q.description
    assert all(qs[qid].path.parent.name == "examples" for qid in EXAMPLES)
    assert all(qs[qid].path.parent.name == "queries" for qid in CODE_OWNED)
    selects = {q.id for q in qs.values() if q.is_select}
    assert selects == set(EXAMPLES) | {"edge/assert_demo_rule", "edge/decision_repo"}


def test_at_most_two_examples():
    assert len([q for q in queries.list_queries() if q.id.startswith("edge/examples/")]) <= 2


def test_render_substitutes_env_and_overrides():
    sql = queries.render_query(
        "edge/examples/zone_traffic", since="2026-01-01T00:00:00Z", until="2026-01-01T00:30:00Z"
    )
    assert "zone_tag = 'zone-test-0123'" in sql
    assert "since = '2026-01-01T00:00:00Z'" in sql and "until = '2026-01-01T00:30:00Z'" in sql
    assert "{{" not in sql
    assert sql.startswith("SELECT")


def test_override_beats_env():
    sql = queries.render_query("edge/assert_demo_rule", cloudflare_zone_id="other-zone")
    assert "zone_id = 'other-zone'" in sql


def test_missing_value_names_the_variable(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ZONE_ID")
    with pytest.raises(ValueError, match="CLOUDFLARE_ZONE_ID"):
        queries.render_query("edge/assert_demo_rule")


def test_restore_template_renders_threshold_and_prefix():
    sql = queries.render_query("edge/restore_rate_limit", threshold=100, demo_prefix="agentic-demo")
    assert sql.startswith("REPLACE cloudflare.rulesets.phases SET rules = ")
    assert '"requests_per_period":100' in sql
    assert '"description":"agentic-demo rate limit (managed by stackql-deploy)"' in sql
    assert "zone_id = 'zone-test-0123' AND ruleset_phase = 'http_ratelimit'" in sql


def test_unknown_query_id():
    with pytest.raises(FileNotFoundError):
        queries.load_query("edge/nope")
    with pytest.raises(FileNotFoundError):
        queries.load_query("edge/tighten_rate_limit")  # the template the model replaced


def test_no_inline_provider_sql_in_agent_code():
    """A SELECT/REPLACE/INSERT on a provider resource may appear only in the gate's allowlist
    check (as the verb and resource constants it matches against), never as a statement."""
    statement = re.compile(
        r"\b(FROM|INTO|REPLACE|UPDATE|DELETE\s+FROM)\s+(cloudflare|github)\.\w+\.\w+"
    )
    offenders = [
        p.name
        for p in (USECASE_DIR / "edge").glob("*.py")
        if p.name != "gate.py" and statement.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders
    gate_src = (USECASE_DIR / "edge" / "gate.py").read_text(encoding="utf-8")
    assert not statement.search(gate_src), "gate.py must hold the allowlist pair, not a statement"
