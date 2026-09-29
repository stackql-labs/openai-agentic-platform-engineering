"""Query library contract: every file parses, ids are unique, declared params match the
placeholders, env-driven rendering works and fails fast, and no file hardcodes tenancy."""

from __future__ import annotations

import re
from collections import Counter

import pytest

from finops.config import PROVIDERS
from finops.queries import QueryError, list_queries, load_query, render_query

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def test_every_query_parses_with_unique_id_and_header():
    qs = list_queries()
    assert len([q for q in qs if q.kind == "select"]) == 8
    assert len([q for q in qs if q.kind == "mutation"]) == 8
    dupes = [k for k, v in Counter(q.id for q in qs).items() if v > 1]
    assert not dupes, dupes
    for q in qs:
        assert q.id.startswith("finops/"), q.id
        assert q.providers and set(q.providers) <= set(PROVIDERS), q.id
        assert q.description, q.id
        assert q.sql.strip(), q.id
        assert not q.sql.rstrip().endswith(";"), q.id
    for q in list_queries(kind="select"):
        assert q.expected_columns, q.id
        assert "est_monthly_usd" in q.expected_columns, q.id
        assert q.sql.lstrip().upper().startswith("SELECT"), (
            q.id
        )  # validate_select_query rejects CTEs


def test_declared_params_match_placeholders():
    for q in list_queries():
        assert set(q.params) == set(PLACEHOLDER.findall(q.sql)), q.id


def test_select_queries_render_from_env(monkeypatch):
    monkeypatch.setenv("SNAPSHOT_MAX_AGE_DAYS", "45")
    for q in list_queries(kind="select"):
        sql = q.render()
        assert "{{" not in sql and not re.search(r"<[a-z_]+>", sql), q.id
    sql = render_query("finops/aws_stale_snapshots")
    assert "'-45 days'" in sql and "'ap-southeast-2'" in sql


def test_snapshot_age_default_applies_without_env(monkeypatch):
    monkeypatch.delenv("SNAPSHOT_MAX_AGE_DAYS", raising=False)
    assert "'-30 days'" in render_query("finops/google_stale_snapshots")


def test_missing_parameter_fails_fast_naming_the_variable(monkeypatch):
    monkeypatch.setenv("GOOGLE_PROJECT", "")
    with pytest.raises(QueryError, match="GOOGLE_PROJECT"):
        render_query("finops/google_unattached_disks")


def test_override_beats_env():
    sql = render_query("finops/aws_unattached_volumes", aws_region="eu-west-1")
    assert "'eu-west-1'" in sql and "ap-southeast-2" not in sql


def test_mutation_templates_render_partially_for_the_model():
    q = load_query("finops/remediation/aws_delete_volume")
    partial = q.render_partial()
    assert "'ap-southeast-2'" in partial and "<volume_id>" in partial
    assert q.sql.upper().startswith("DELETE")
    with pytest.raises(QueryError, match="VOLUME_ID"):
        q.render()
    assert load_query("finops/remediation/aws_release_address").sql.upper().startswith("EXEC")


def test_no_hardcoded_tenancy_identifiers():
    for q in list_queries():
        assert not re.search(r"\b\d{12}\b", q.sql), f"{q.id} hardcodes an AWS account id"
        assert not re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", q.sql
        ), f"{q.id} hardcodes a GUID"


def test_select_queries_are_pinned_to_tenancy():
    pins = {"aws": "aws_region", "azure": "azure_subscription_id", "google": "google_project"}
    for q in list_queries(kind="select"):
        for p in q.providers:
            assert pins[p] in q.params, f"{q.id} is not pinned to the {p} demo tenancy"
