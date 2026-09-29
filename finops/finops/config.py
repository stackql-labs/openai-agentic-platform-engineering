"""Environment-driven configuration.

Everything comes from the single `.env` at the repo root (shared by every use case in this
repo). Nothing here is hardcoded to a model, account, subscription or project. Missing required
values fail fast with the variable name so the fix is obvious.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PACKAGE_DIR = Path(__file__).resolve().parent  # finops/finops
USECASE_DIR = PACKAGE_DIR.parent  # finops/
REPO_ROOT = USECASE_DIR.parent  # repo root: .env, pricing.json, runs/
ENV_FILE = REPO_ROOT / ".env"
QUERIES_DIR = USECASE_DIR / "queries"
RUNS_DIR = REPO_ROOT / "runs"
PRICING_FILE = REPO_ROOT / "pricing.json"

load_dotenv(ENV_FILE, override=False)

PROVIDERS = ("aws", "azure", "google")

# Credentials StackQL reads from the process environment, per provider. A provider is swept only
# when all of these are present (or when the operator names it with --providers).
PROVIDER_CREDENTIAL_ENV = {
    "aws": ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"),
    "azure": ("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"),
    "google": ("GOOGLE_CREDENTIALS",),
}

# Tenancy pins every query needs for that provider (fail fast if missing).
PROVIDER_TENANCY_ENV = {
    "aws": ("AWS_REGION",),
    "azure": ("AZURE_SUBSCRIPTION_ID",),
    "google": ("GOOGLE_PROJECT",),
}


class ConfigError(RuntimeError):
    pass


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    val = os.environ.get(name, "")
    if val == "" and default is not None:
        val = default
    if required and val == "":
        raise ConfigError(f"{name} is not set - add it to {ENV_FILE} (see .env.example)")
    return val


@dataclass(frozen=True)
class ModelTier:
    name: str  # "sweep" | "reasoning"
    model: str
    reasoning_effort: str


@dataclass(frozen=True)
class Settings:
    stackql_approot: Path
    mcp_audit_log: Path
    demo_prefix: str
    demo_tag_key: str
    demo_tag_value: str
    aws_region: str
    azure_subscription_id: str
    google_project: str
    snapshot_max_age_days: int

    # Model ids are read when a tier is first used, so `setup` and `validate` (no model) work
    # before the operator has chosen models.
    @property
    def sweep(self) -> ModelTier:
        return ModelTier(
            "sweep", env("SWEEP_MODEL", required=True), env("SWEEP_REASONING_EFFORT", "low")
        )

    @property
    def reasoning(self) -> ModelTier:
        return ModelTier(
            "reasoning",
            env("REASONING_MODEL", required=True),
            env("REASONING_REASONING_EFFORT", "medium"),
        )


@lru_cache(maxsize=1)
def settings() -> Settings:
    approot = Path(env("STACKQL_APPROOT", "~/.stackql")).expanduser()
    if not approot.is_absolute():
        approot = REPO_ROOT / approot
    audit = Path(env("STACKQL_MCP_AUDIT_LOG", "runs/stackql-mcp-audit.jsonl"))
    if not audit.is_absolute():
        audit = REPO_ROOT / audit
    raw_days = env("SNAPSHOT_MAX_AGE_DAYS", "30")
    try:
        days = int(raw_days)
    except ValueError as e:
        raise ConfigError(f"SNAPSHOT_MAX_AGE_DAYS must be an integer, got {raw_days!r}") from e
    return Settings(
        stackql_approot=approot,
        mcp_audit_log=audit,
        demo_prefix=env("DEMO_PREFIX", "agentic-demo"),
        demo_tag_key=env("DEMO_TAG_KEY", "purpose"),
        demo_tag_value=env("DEMO_TAG_VALUE", "agentic-demo"),
        aws_region=env("AWS_REGION"),
        azure_subscription_id=env("AZURE_SUBSCRIPTION_ID"),
        google_project=env("GOOGLE_PROJECT"),
        snapshot_max_age_days=days,
    )


def param_defaults() -> dict[str, str]:
    """Query parameters that have a documented default when the env variable is unset."""
    return {"snapshot_max_age_days": str(settings().snapshot_max_age_days)}


def provider_configured(provider: str) -> bool:
    """True when the credentials StackQL needs for this provider are present in the environment."""
    return all(env(v) != "" for v in PROVIDER_CREDENTIAL_ENV.get(provider, ()))


def configured_providers() -> list[str]:
    return [p for p in PROVIDERS if provider_configured(p)]


def missing_tenancy(provider: str) -> list[str]:
    return [v for v in PROVIDER_TENANCY_ENV.get(provider, ()) if env(v) == ""]
