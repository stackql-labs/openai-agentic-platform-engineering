"""The prompt loader: files are found, placeholders substitute from values and env, a missing
value fails naming the variable, the discovery briefing is appended and carries the server's
instructions when given, and no prompt names a resource, column or SQL."""

from __future__ import annotations

import re

import pytest

from edge import prompts
from edge.config import PROMPTS_DIR

VALUES = dict(
    since="2026-01-01T00:00:00Z",
    until="2026-01-01T00:30:00Z",
    window_minutes=30,
    window_seconds=1800,
    elevated_rps=5.0,
    tightened_threshold=30,
    baseline_threshold=100,
    rate_limit_period=10,
)


def test_prompt_files_exist_with_expected_placeholders():
    assert {p.name for p in PROMPTS_DIR.glob("*.md")} == {"recon.md", "decide.md", "discovery.md"}
    assert prompts.placeholders("recon") == [
        "cloudflare_zone_id",
        "since",
        "until",
        "window_minutes",
        "window_seconds",
        "demo_prefix",
    ]
    assert set(prompts.placeholders("decide")) == {
        "cloudflare_zone_id",
        "elevated_rps",
        "tightened_threshold",
        "demo_prefix",
        "rate_limit_period",
        "baseline_threshold",
    }
    assert prompts.placeholders("discovery") == []


def test_render_substitutes_values_and_env():
    text = prompts.render_prompt("recon", **VALUES)  # zone id and prefix come from the env
    assert "zone `zone-test-0123`" in text
    assert "contains `agentic-demo`" in text
    assert "2026-01-01T00:00:00Z -> 2026-01-01T00:30:00Z (30 minutes, 1800 seconds)" in text
    assert "{{" not in text and "}}" not in text


def test_explicit_value_beats_env():
    text = prompts.render_prompt("decide", cloudflare_zone_id="other-zone", **VALUES)
    assert "other-zone" in text and "zone-test-0123" not in text


def test_missing_value_names_the_variable(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_ZONE_ID")
    with pytest.raises(ValueError, match="cloudflare_zone_id \\(set CLOUDFLARE_ZONE_ID\\)"):
        prompts.render_prompt("recon", **VALUES)


def test_unknown_prompt_or_role():
    with pytest.raises(FileNotFoundError):
        prompts.render_prompt("nope")
    with pytest.raises(ValueError):
        prompts.instructions_for("nope", "briefing")


def test_briefing_is_appended_with_server_instructions():
    plain = prompts.discovery_briefing(None)
    assert plain.startswith("# Working with the StackQL tools")
    assert "stackql://docs/instructions, read at startup" not in plain
    with_server = prompts.discovery_briefing("# Overview\n\nserver text")
    assert with_server.startswith(plain)
    assert "# Server instructions (stackql://docs/instructions, read at startup)" in with_server
    assert with_server.endswith("server text")
    full = prompts.instructions_for("decide", with_server, **VALUES)
    assert full.startswith("# Decide:") and full.endswith("server text")


def test_prompts_carry_intent_not_resources_or_sql():
    """The prompts must not enumerate resource names, columns or statements: the agents discover
    those through the tools. Tool names and the demo phase word are the only structure allowed."""
    sql_shape = re.compile(
        r"\b(SELECT\s+\S+.*\bFROM\s+\S+|REPLACE\s+\S+\s+SET\b|INSERT\s+INTO\b|UPDATE\s+\S+\s+SET\b|DELETE\s+FROM\b)"
    )
    resource_name = re.compile(r"\b(cloudflare|github)\.[a-z_]+\.[a-z_]+")
    for name in ("recon", "decide", "discovery"):
        text = prompts.prompt_path(name).read_text(encoding="utf-8")
        assert not resource_name.search(text), name
        assert not sql_shape.search(text), name
        assert "http_requests_adaptive_groups" not in text and "rulesets.phases" not in text
        assert len(text.splitlines()) <= 60, name
    for name in ("recon", "decide"):
        text = prompts.prompt_path(name).read_text(encoding="utf-8")
        assert "query_library" in text or "query library" in text, name
