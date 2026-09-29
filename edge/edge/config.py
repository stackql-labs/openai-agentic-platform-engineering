"""Environment-driven configuration. Model ids, the zone id, thresholds and paths all come from the
repo-root .env (see .env.example); nothing is hardcoded. Values that are required only at the point
of use (a model id, the zone id) are checked there, so `setup`, `validate` and the tests do not
need a filled-in .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

USECASE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = USECASE_DIR.parent
ENV_FILE = REPO_ROOT / ".env"
QUERIES_DIR = USECASE_DIR / "queries"
RUNS_DIR = REPO_ROOT / "runs"
PRICING_FILE = REPO_ROOT / "pricing.json"

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


def env_float(name: str, default: float) -> float:
    raw = env(name, str(default))
    try:
        return float(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from e


def _path(name: str, default: str, base: Path) -> Path:
    p = Path(env(name, default)).expanduser()
    return p if p.is_absolute() else base / p


@dataclass(frozen=True)
class ModelTier:
    name: str  # "sweep" | "reasoning"
    model: str  # "" until SWEEP_MODEL / REASONING_MODEL is set; checked at use
    reasoning_effort: str


@dataclass(frozen=True)
class Settings:
    sweep: ModelTier
    reasoning: ModelTier
    stackql_approot: Path
    mcp_audit_log: Path
    demo_prefix: str
    cloudflare_zone_id: str
    elevated_rps: float
    tightened_threshold: int
    baseline_threshold: int
    rate_limit_period: int
    window_minutes: int
    github_decisions_repo: str
    decisions_log: Path

    @property
    def rule_description(self) -> str:
        """The description the stack writes on the demo rule; the gate asserts DEMO_PREFIX in it."""
        return f"{self.demo_prefix} rate limit (managed by stackql-deploy)"

    @property
    def allowed_thresholds(self) -> frozenset[int]:
        """The only thresholds the gate will render: the policy's two values, nothing else."""
        return frozenset({self.tightened_threshold, self.baseline_threshold})


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings(
        sweep=ModelTier("sweep", env("SWEEP_MODEL"), env("SWEEP_REASONING_EFFORT", "low")),
        reasoning=ModelTier(
            "reasoning", env("REASONING_MODEL"), env("REASONING_REASONING_EFFORT", "medium")
        ),
        stackql_approot=_path("STACKQL_APPROOT", "~/.stackql", REPO_ROOT),
        mcp_audit_log=_path("STACKQL_MCP_AUDIT_LOG", "runs/stackql-mcp-audit.jsonl", REPO_ROOT),
        demo_prefix=env("DEMO_PREFIX", "agentic-demo"),
        cloudflare_zone_id=env("CLOUDFLARE_ZONE_ID"),
        elevated_rps=env_float("ELEVATED_RPS", 5.0),
        tightened_threshold=env_int("TIGHTENED_THRESHOLD", 30),
        baseline_threshold=env_int("BASELINE_THRESHOLD", 100),
        rate_limit_period=10,
        window_minutes=env_int("WINDOW_MINUTES", 30),
        github_decisions_repo=env("GITHUB_DECISIONS_REPO"),
        decisions_log=_path("EDGE_DECISIONS_LOG", "runs/edge-decisions.jsonl", REPO_ROOT),
    )


def reset_settings() -> None:
    """Drop the cached Settings (tests change the environment between cases)."""
    settings.cache_clear()
