"""The example queries: every file parses with a header, ids are unique and namespaced under
finops/examples/, declared params match the placeholders, env-driven rendering works and fails
fast, no file hardcodes tenancy, and the set stays small (examples, not a pack)."""

from __future__ import annotations

import re
from collections import Counter

import pytest

from finops.config import PROVIDERS, QUERIES_DIR
from finops.queries import QueryError, list_queries, load_query, render_query

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def test_examples_are_few_and_live_under_examples():
    qs = list_queries()
    assert 2 <= len(qs) <= 3, [q.id for q in qs]
    for q in qs:
        assert q.path.parent == QUERIES_DIR / "examples", q.path
        assert q.id == f"finops/examples/{q.path.stem}", q.id
    assert not (QUERIES_DIR / "remediation").exists()
    dupes = [k for k, v in Counter(q.id for q in qs).items() if v > 1]
    assert not dupes, dupes


def test_every_example_parses_with_a_header():
    for q in list_queries():
        assert q.providers and set(q.providers) <= set(PROVIDERS), q.id
        assert q.description and q.expected_columns, q.id
        assert "est_monthly_usd" in q.expected_columns, q.id
        assert q.sql.strip() and not q.sql.rstrip().endswith(";"), q.id
        assert q.sql.lstrip().upper().startswith("SELECT"), q.id  # flat SELECT, no CTE
        assert set(q.params) == set(PLACEHOLDER.findall(q.sql)), q.id


def test_examples_render_from_env(monkeypatch):
    monkeypatch.setenv("SNAPSHOT_MAX_AGE_DAYS", "45")
    for q in list_queries():
        sql = q.render()
        assert "{{" not in sql, q.id
    sql = render_query("finops/examples/google_stale_snapshots")
    assert "'-45 days'" in sql and "'demo-project'" in sql


def test_snapshot_age_default_applies_without_env(monkeypatch):
    monkeypatch.delenv("SNAPSHOT_MAX_AGE_DAYS", raising=False)
    assert "'-30 days'" in render_query("finops/examples/google_stale_snapshots")


def test_missing_parameter_fails_fast_naming_the_variable(monkeypatch):
    monkeypatch.setenv("GOOGLE_PROJECT", "")
    with pytest.raises(QueryError, match="GOOGLE_PROJECT"):
        render_query("finops/examples/google_stale_snapshots")
    with pytest.raises(FileNotFoundError):
        load_query("finops/aws_unattached_volumes")


def test_override_beats_env():
    sql = render_query("finops/examples/aws_unattached_volumes", aws_region="eu-west-1")
    assert "'eu-west-1'" in sql and "ap-southeast-2" not in sql


def test_examples_are_pinned_to_tenancy_and_hardcode_nothing():
    pins = {"aws": "aws_region", "azure": "azure_subscription_id", "google": "google_project"}
    for q in list_queries():
        for p in q.providers:
            assert pins[p] in q.params, f"{q.id} is not pinned to the {p} demo tenancy"
        assert not re.search(r"\b\d{12}\b", q.sql), f"{q.id} hardcodes an AWS account id"
        assert not re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", q.sql
        ), f"{q.id} hardcodes a GUID"
