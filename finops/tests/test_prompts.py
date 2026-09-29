"""Prompt loader: the role prompts exist and render with dummy env, placeholders come from
overrides then env then the documented defaults, a missing value fails naming the variable, the
prompts carry intent rather than SQL or resource names, and the discovery briefing appends the
server's instructions (or records that it could not)."""

from __future__ import annotations

import re

import pytest

from finops.config import PROMPTS_DIR
from finops.prompts import (
    SERVER_INSTRUCTIONS_URI,
    PromptError,
    discovery_briefing,
    load_prompt,
    placeholders,
    render_prompt,
)

ROLE_PROMPTS = ("sweep", "reasoning", "discovery")
SQL_VERB = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|REPLACE|EXEC)\s+(\*|FROM|INTO|[a-z_]+\.[a-z_]+\.[a-z_]+)\b"
)


def test_prompt_files_exist_and_are_short():
    for name in ROLE_PROMPTS:
        text = load_prompt(name)
        assert text.strip(), name
        assert len(text.splitlines()) <= 60, f"{name}.md is longer than 60 lines"
        assert text.isascii(), f"{name}.md must be plain ASCII"


def test_declared_placeholders():
    assert placeholders(load_prompt("sweep")) == [
        "demo_prefix",
        "demo_tag_key",
        "demo_tag_value",
        "providers",
        "snapshot_max_age_days",
        "tenancy",
    ]
    assert placeholders(load_prompt("reasoning")) == [
        "demo_prefix",
        "demo_tag_key",
        "demo_tag_value",
        "provider",
        "tenancy",
    ]
    assert placeholders(load_prompt("discovery")) == []


def test_prompts_state_intent_not_sql():
    for name in ROLE_PROMPTS:
        text = load_prompt(name)
        assert not SQL_VERB.search(text), f"{name}.md contains a SQL statement"
        assert "provider.service.resource" in text or name != "discovery"
    sweep = load_prompt("sweep")
    for word in ("query_library_search", "list_resources", "describe_resource", "estimate"):
        assert word in sweep
    reasoning = load_prompt("reasoning")
    for word in ("describe_method", "list_methods", "deferred"):
        assert word in reasoning
    discovery = load_prompt("discovery")
    for word in ("validate_select_query", "json_extract", "WITH", "read_only"):
        assert word in discovery
    # no resource names are enumerated for the model
    for name in ROLE_PROMPTS:
        assert not re.search(r"\b(aws|azure|google)\.[a-z_]+\.[a-z_]+", load_prompt(name)), name


def test_render_substitutes_from_overrides_env_and_defaults(monkeypatch):
    monkeypatch.setenv("SNAPSHOT_MAX_AGE_DAYS", "45")
    monkeypatch.delenv("DEMO_PREFIX", raising=False)
    text = render_prompt("sweep", providers="aws", tenancy="- aws: region ap-southeast-2")
    assert "{{" not in text
    assert "older than 45 days" in text
    assert "aws: region ap-southeast-2" in text
    assert "purpose=agentic-demo" in text and "starting\nwith agentic-demo" in text


def test_override_beats_env(monkeypatch):
    monkeypatch.setenv("PROVIDER", "azure")
    text = render_prompt("reasoning", provider="google", tenancy="- google: project p")
    assert "(google)" in text and "azure" not in text


def test_missing_placeholder_fails_naming_the_variable(monkeypatch):
    monkeypatch.delenv("TENANCY", raising=False)
    with pytest.raises(PromptError, match=r"\{\{ tenancy \}\} \(env TENANCY\)"):
        render_prompt("sweep", providers="aws")
    with pytest.raises(PromptError, match="not found"):
        render_prompt("no_such_prompt")


def test_render_from_another_directory(tmp_path):
    (tmp_path / "x.md").write_text("hello {{ who }}", encoding="utf-8")
    assert render_prompt("x", tmp_path, who="world") == "hello world"


class _Content:
    def __init__(self, text):
        self.text = text


class _Res:
    def __init__(self, texts):
        self.contents = [_Content(t) for t in texts]


class _Server:
    def __init__(self, texts=None, fail=False):
        self.texts, self.fail, self.uris = texts or [], fail, []

    async def read_resource(self, uri):
        self.uris.append(uri)
        if self.fail:
            raise RuntimeError("no resources")
        return _Res(self.texts)


async def test_discovery_briefing_appends_server_instructions():
    server = _Server(["# Overview\n\nStackQL exposes providers as SQL."])
    notes: list[str] = []
    text = await discovery_briefing(server, PROMPTS_DIR, notes=notes)
    assert server.uris == [SERVER_INSTRUCTIONS_URI]
    assert text.startswith(load_prompt("discovery").strip())
    assert f"## StackQL server instructions ({SERVER_INSTRUCTIONS_URI})" in text
    assert text.endswith("StackQL exposes providers as SQL.") and notes == []


async def test_discovery_briefing_continues_without_the_resource():
    notes: list[str] = []
    text = await discovery_briefing(_Server(fail=True), PROMPTS_DIR, notes=notes)
    assert text == load_prompt("discovery").strip()
    assert len(notes) == 1 and SERVER_INSTRUCTIONS_URI in notes[0] and "RuntimeError" in notes[0]
    notes.clear()
    text = await discovery_briefing(_Server([""]), PROMPTS_DIR, notes=notes)
    assert "server instructions" not in text and "no text" in notes[0]
