# 01 - Universal-interface hook (2 min)

## State required

`.env` filled in; `make setup` done; the seeded estate is not needed for this segment.

## Commands

```
make smoke                      # live, about 20 s
uv run python -m oape_agents.smoke --fallback   # replay
```

## Talk track

- 0:00 Start the command before speaking. "One agent. One MCP server. One SQL dialect. Four cloud control planes." Show `queries/smoke/` - four files, each a SELECT with the tenancy pinned in the WHERE clause.
- 0:30 Point at the provider list the agent returns: AWS, Azure, Google, GitHub, Entra ID, Okta, OpenAI's own admin API. "Cloud and SaaS APIs as data sources accessed via SQL. Discovery is part of the surface: list_resources, describe_resource, list_methods."
- 1:00 The row counts come back. "Every call the model made is one logged SQL statement" - open `runs/stackql-mcp-audit.jsonl` and show the four `run_select_query` records.
- 1:30 Cost table: one mini-tier model, five tool calls, well under a cent. Trace URL. "This is the unit of work we will scale up for the rest of the session."

## Fallback trigger

No first tool call within 30 s, or any provider error: `--fallback`. The recorded run shows 4 queries, 8 providers, USD 0.0057.
