"""Test environment: tenancy pins and model ids set before finops.config loads, so nothing
here depends on a .env at the repo root. No network, no OpenAI, no cloud credentials."""

from __future__ import annotations

import os
import tempfile

_DEFAULTS = {
    "SWEEP_MODEL": "test-sweep-model",
    "REASONING_MODEL": "test-reasoning-model",
    "AWS_REGION": "ap-southeast-2",
    "AZURE_SUBSCRIPTION_ID": "00000000-0000-0000-0000-000000000000",
    "GOOGLE_PROJECT": "demo-project",
    "STACKQL_APPROOT": os.path.join(tempfile.gettempdir(), "finops-test-approot"),
    "STACKQL_MCP_AUDIT_LOG": os.path.join(tempfile.gettempdir(), "finops-test-audit.jsonl"),
}
for k, v in _DEFAULTS.items():
    os.environ.setdefault(k, v)
os.environ.pop("SNAPSHOT_MAX_AGE_DAYS", None)
os.environ.pop("OPENAI_PRICING_JSON", None)

import pytest  # noqa: E402

from finops.config import settings  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_settings():
    settings.cache_clear()
    yield
    settings.cache_clear()
