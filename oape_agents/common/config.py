"""Environment-driven configuration. Nothing here is hardcoded to a model, account or org:
everything comes from .env (see .env.example). Missing required values fail fast with the
variable name so the fix is obvious."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"
QUERIES_DIR = REPO_ROOT / "queries"
FALLBACKS_DIR = REPO_ROOT / "fallbacks"
RUNS_DIR = REPO_ROOT / "runs"
CONFIG_DIR = REPO_ROOT / "config"

load_dotenv(ENV_FILE, override=False)


class ConfigError(RuntimeError):
    pass


def env(name: str, default: str | None = None, *, required: bool = False) -> str:
    val = os.environ.get(name, "")
    if val == "" and default is not None:
        val = default
    if required and val == "":
        raise ConfigError(f"{name} is not set - add it to {ENV_FILE} (see .env.example)")
    return val


def env_int(name: str, default: int) -> int:
    raw = env(name, str(default))
    try:
        return int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from e


def env_bool(name: str, default: bool = False) -> bool:
    return env(name, "1" if default else "0").lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ModelTier:
    name: str  # "sweep" | "reasoning"
    model: str
    reasoning_effort: str


@dataclass(frozen=True)
class Settings:
    sweep: ModelTier
    reasoning: ModelTier
    stackql_bin: str
    stackql_approot: Path
    mcp_audit_log: Path
    snapshot_db: Path
    demo_tag_key: str
    demo_tag_value: str
    demo_prefix: str
    aws_account_id: str
    aws_region: str
    azure_subscription_id: str
    azure_location: str
    google_project: str
    google_region: str
    google_zone: str
    github_org: str
    github_issues_repo: str
    escalation_severity: str
    openai_key_max_age_days: int
    deploy_key_max_age_days: int
    idp_privileged_group: str

    @property
    def tenancy(self) -> dict[str, str]:
        """Parameters substituted into every query - the demo tenancy pins."""
        return {
            "aws_account_id": self.aws_account_id,
            "aws_region": self.aws_region,
            "azure_subscription_id": self.azure_subscription_id,
            "azure_location": self.azure_location,
            "google_project": self.google_project,
            "google_region": self.google_region,
            "google_zone": self.google_zone,
            "github_org": self.github_org,
            "demo_tag_key": self.demo_tag_key,
            "demo_tag_value": self.demo_tag_value,
            "demo_prefix": self.demo_prefix,
            "demo_rg": f"{self.demo_prefix}-rg",
            "aws_iam_region": "us-east-1",
            "s3_bucket_prefix": self.demo_prefix,
            "checkout_asg_name": f"{self.demo_prefix}-checkout-asg",
            "checkout_service_tag": "oape-checkout",
            "checkout_repo": f"{self.demo_prefix}-checkout",
            "snapshot_max_age_days": env("SNAPSHOT_MAX_AGE_DAYS", "30"),
            "recent_deploy_hours": env("RECENT_DEPLOY_HOURS", "72"),
            "openai_key_max_age_days": str(self.openai_key_max_age_days),
            "deploy_key_max_age_days": str(self.deploy_key_max_age_days),
            "idp_privileged_group": self.idp_privileged_group,
            "okta_subdomain": env("OKTA_DOMAIN").split(".")[0] if env("OKTA_DOMAIN") else "",
            "openai_org_id": env("OPENAI_ORG_ID"),
        }


@lru_cache(maxsize=1)
def settings() -> Settings:
    approot = Path(env("STACKQL_APPROOT", ".stackql"))
    if not approot.is_absolute():
        approot = REPO_ROOT / approot
    audit = Path(env("STACKQL_MCP_AUDIT_LOG", "runs/stackql-mcp-audit.jsonl"))
    if not audit.is_absolute():
        audit = REPO_ROOT / audit
    snap = Path(env("SNAPSHOT_DB", "snapshots/estate.db"))
    if not snap.is_absolute():
        snap = REPO_ROOT / snap
    return Settings(
        sweep=ModelTier(
            "sweep", env("SWEEP_MODEL", required=True), env("SWEEP_REASONING_EFFORT", "low")
        ),
        reasoning=ModelTier(
            "reasoning",
            env("REASONING_MODEL", required=True),
            env("REASONING_REASONING_EFFORT", "medium"),
        ),
        stackql_bin=env("STACKQL_BIN", "stackql"),
        stackql_approot=approot,
        mcp_audit_log=audit,
        snapshot_db=snap,
        demo_tag_key=env("DEMO_TAG_KEY", "purpose"),
        demo_tag_value=env("DEMO_TAG_VALUE", "oape-demo"),
        demo_prefix=env("DEMO_PREFIX", "oape-demo"),
        aws_account_id=env("AWS_ACCOUNT_ID"),
        aws_region=env("AWS_REGION", "ap-southeast-2"),
        azure_subscription_id=env("AZURE_SUBSCRIPTION_ID"),
        azure_location=env("AZURE_LOCATION", "eastus2"),
        google_project=env("GOOGLE_PROJECT"),
        google_region=env("GOOGLE_REGION", "us-central1"),
        google_zone=env("GOOGLE_ZONE", "us-central1-a"),
        github_org=env("GITHUB_ORG"),
        github_issues_repo=env("GITHUB_ISSUES_REPO"),
        escalation_severity=env("ESCALATION_SEVERITY", "high"),
        openai_key_max_age_days=env_int("OPENAI_KEY_MAX_AGE_DAYS", 0),
        deploy_key_max_age_days=env_int("DEPLOY_KEY_MAX_AGE_DAYS", 0),
        idp_privileged_group=env("IDP_PRIVILEGED_GROUP", "oape-demo-cloud-admins"),
    )


PROVIDER_ENV = {
    "aws": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_ACCOUNT_ID"],
    "azure": ["AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_SUBSCRIPTION_ID"],
    "google": ["GOOGLE_CREDENTIALS", "GOOGLE_PROJECT"],
    "github": ["STACKQL_GITHUB_USERNAME", "STACKQL_GITHUB_PASSWORD", "GITHUB_ORG"],
    "entra_id": ["AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"],
    "okta": ["OKTA_DOMAIN", "OKTA_API_TOKEN"],
    "openai_admin": ["OPENAI_ADMIN_KEY"],
}


def provider_configured(provider: str) -> bool:
    """True when the credentials StackQL needs for this provider are present in the environment."""
    return all(env(v) != "" for v in PROVIDER_ENV.get(provider, []))
