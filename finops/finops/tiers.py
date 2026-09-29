"""Model tiering. The mini tier (SWEEP_MODEL) runs the scheduled sweep and classification; the
frontier tier (REASONING_MODEL) plans remediation per provider. Model ids come from .env only.

An agent's instructions are a rendered role prompt from finops/prompts/ followed by the shared
discovery briefing (finops/prompts/discovery.md plus the server's own instructions). There is
no dialect cheat sheet in code: the briefing is the short list in discovery.md and the models
discover resources and IO contracts through the StackQL tools."""

from __future__ import annotations

from typing import Any

from agents import Agent, ModelSettings
from agents.model_settings import Reasoning

from .config import ModelTier, settings


def tier(name: str) -> ModelTier:
    s = settings()
    if name == "sweep":
        return s.sweep
    if name == "reasoning":
        return s.reasoning
    raise ValueError(f"unknown tier {name!r}")


def model_settings_for(t: ModelTier, **overrides: Any) -> ModelSettings:
    kwargs: dict[str, Any] = {
        "reasoning": Reasoning(effort=t.reasoning_effort),
        "parallel_tool_calls": True,
    }
    kwargs.update(overrides)
    return ModelSettings(**kwargs)


def make_agent(
    tier_name: str,
    *,
    name: str,
    instructions: str,
    briefing: str = "",
    mcp_servers: list | None = None,
    tools: list | None = None,
    output_type: type | None = None,
    **model_overrides: Any,
) -> Agent:
    """Construct an Agent on the named tier. The model id is never a literal in agent code."""
    t = tier(tier_name)
    full = f"{instructions}\n\n{briefing}" if briefing else instructions
    return Agent(
        name=name,
        instructions=full,
        model=t.model,
        model_settings=model_settings_for(t, **model_overrides),
        mcp_servers=mcp_servers or [],
        tools=tools or [],
        output_type=output_type,
    )
