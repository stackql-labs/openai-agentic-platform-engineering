"""Query library contract: every file parses, ids are unique, declared params match placeholders,
tenancy placeholders render, and no query file refers to a hardcoded account, subscription,
project or org."""

from __future__ import annotations

import re
from collections import Counter

from oape_agents.common.config import settings
from oape_agents.common.queries import list_queries, render_union, sql_list

PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


def test_every_query_has_a_header_and_unique_id():
    qs = list_queries()
    assert len(qs) >= 40
    ids = Counter(q.id for q in qs)
    dupes = [k for k, v in ids.items() if v > 1]
    assert not dupes, dupes
    for q in qs:
        assert q.scenario, q.id
        assert q.providers, q.id
        assert q.description, q.id
        assert q.sql.strip(), q.id


def test_declared_params_match_placeholders():
    for q in list_queries():
        used = set(PLACEHOLDER.findall(q.sql))
        declared = set(q.params)
        assert used == declared, f"{q.id}: used {sorted(used)} declared {sorted(declared)}"


def test_static_queries_render_with_tenancy():
    tenancy = settings().tenancy
    for q in list_queries():
        if all(p in tenancy and tenancy[p] != "" for p in q.params):
            sql = q.render()
            assert "{{" not in sql, q.id


def test_no_hardcoded_tenancy_identifiers():
    for q in list_queries():
        assert not re.search(r"\b\d{12}\b", q.sql), f"{q.id} hardcodes an AWS account id"
        assert not re.search(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            q.sql.replace("8e3af657-a8ff-443c-a75c-2fe8c4bcb635", ""),
        ), f"{q.id} hardcodes a GUID"


def test_helpers():
    assert sql_list(["a", "b'c"]) == "'a', 'b''c'"
    u = render_union("entitlements/aws_admin_user_literal", "user_name", ["u1", "u2"])
    assert u.count("UNION ALL") == 1 and "'u1'" in u and "'u2'" in u


def test_mutation_templates_only_under_triage():
    for q in list_queries():
        verb = q.sql.lstrip().split()[0].upper()
        if verb in ("UPDATE", "INSERT", "DELETE", "EXEC", "REPLACE"):
            assert q.scenario == "triage", q.id
