"""Offline test fixtures: a demo environment so nothing depends on the operator's .env."""

from __future__ import annotations

import pytest

from edge.config import reset_settings


@pytest.fixture(autouse=True)
def demo_env(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_ZONE_ID", "zone-test-0123")
    monkeypatch.setenv("DEMO_PREFIX", "agentic-demo")
    monkeypatch.setenv("TIGHTENED_THRESHOLD", "30")
    monkeypatch.setenv("BASELINE_THRESHOLD", "100")
    monkeypatch.setenv("ELEVATED_RPS", "5")
    monkeypatch.delenv("GITHUB_DECISIONS_REPO", raising=False)
    reset_settings()
    yield
    reset_settings()
