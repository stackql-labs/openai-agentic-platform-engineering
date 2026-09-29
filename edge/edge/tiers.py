"""Model tiering. The mini tier (SWEEP_MODEL) runs recon; the frontier tier (REASONING_MODEL)
makes the decision. Model ids come from .env and are never literals in code."""

from __future__ import annotations

from typing import Any

from agents import Agent, ModelSettings
from agents.model_settings import Reasoning

from .config import ConfigError, ModelTier, settings


def tier(name: str) -> ModelTier:
    s = settings()
    t = {"sweep": s.sweep, "reasoning": s.reasoning}.get(name)
    if t is None:
        raise ValueError(f"unknown tier {name!r}")
    if not t.model:
        var = "SWEEP_MODEL" if name == "sweep" else "REASONING_MODEL"
        raise ConfigError(f"{var} is not set - add it to the repo-root .env (see .env.example)")
    return t


def model_settings_for(t: ModelTier) -> ModelSettings:
    return ModelSettings(reasoning=Reasoning(effort=t.reasoning_effort))


def make_agent(
    tier_name: str,
    *,
    name: str,
    instructions: str,
    mcp_servers: list[Any] | None = None,
    output_type: type | None = None,
) -> Agent:
    t = tier(tier_name)
    return Agent(
        name=name,
        instructions=instructions,
        model=t.model,
        model_settings=model_settings_for(t),
        mcp_servers=mcp_servers or [],
        output_type=output_type,
    )
