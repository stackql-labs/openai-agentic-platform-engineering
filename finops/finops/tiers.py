"""Model tiering. The mini tier (SWEEP_MODEL) runs the scheduled sweep and classification; the
frontier tier (REASONING_MODEL) plans remediation per provider. Model ids come from .env only."""

from __future__ import annotations

from typing import Any

from agents import Agent, ModelSettings
from agents.model_settings import Reasoning

from .config import ModelTier, settings

# These notes encode constraints found while validating the query pack live. Keep them with
# the instructions of any agent that is allowed to compose SQL.
DIALECT_BRIEFING = """StackQL dialect notes (SQLite backend):
- Providers are queried as provider.service.resource. Use list_resources / describe_resource /
  list_methods before writing SQL against an unfamiliar resource instead of guessing names.
- AWS resources need `region = '...'` in the WHERE clause. Azure resources need `subscription_id`
  and usually `resource_group_name`. Google resources need `project` (and `zone` for zonal ones).
- JSON columns are unpacked with JSON_EXTRACT(col, '$.path') and json_each(col) - put the filtered
  provider query in a CTE first, then unpack.
- Booleans compare as 0/1 or 'true'/'false' depending on provider; numbers may arrive as text.
- An empty result is zero rows, not an error. Do not retry a query that returned zero rows.
- One CTE level at most: `WITH x AS (<provider select>) SELECT ... FROM x[, json_each(x.col) j]`.
  Deeper nesting, subqueries in IN (...), and aggregates over json_each rows fail.
- WHERE predicates on json_each output are NOT applied. Classification of unnested JSON rows is
  a projected CASE column; filter on that column in your reasoning.
- Put filters on provider columns inside the provider SELECT. Avoid double quotes inside LIKE
  patterns. Compare dates with julianday(col) >= julianday('now', '-N days'), not arithmetic.
- Fan-out parameters (IN lists) must be literals; a second query renders them from a first.
- validate_select_query does not accept WITH ... AS (CTEs); the canonical queries are flat SELECTs.
- Prefer the canonical queries you are given. Only compose new SQL when a query is missing."""


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
    mcp_servers: list | None = None,
    tools: list | None = None,
    output_type: type | None = None,
    include_dialect_notes: bool = True,
    **model_overrides: Any,
) -> Agent:
    """Construct an Agent on the named tier. The model id is never a literal in agent code."""
    t = tier(tier_name)
    full = f"{instructions}\n\n{DIALECT_BRIEFING}" if include_dialect_notes else instructions
    return Agent(
        name=name,
        instructions=full,
        model=t.model,
        model_settings=model_settings_for(t, **model_overrides),
        mcp_servers=mcp_servers or [],
        tools=tools or [],
        output_type=output_type,
    )
