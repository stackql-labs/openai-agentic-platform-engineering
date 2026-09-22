# 07 - OpenAI estate closer (3 min)

## State required

`OPENAI_ADMIN_KEY` set (an Admin API key from Organization settings) and `make seed --only openai` run, which plants two projects and a service account key. Without the key the segment runs from the fallback, which is synthetic and says so on screen (see WORK_ORDER.md).

## Commands

```
make openai-estate                                            # live
uv run python -m oape_agents.openai_estate.estate --fallback  # replay
```

## Talk track

- 0:00 "Last one. The same pattern pointed at the organization that runs the agents." Start the run.
- 0:30 Open `queries/openai_estate/`: projects, service accounts and API keys per project with real age against `OPENAI_KEY_MAX_AGE_DAYS`, costs and completions usage by project from the admin API.
- 1:15 The brief: projects with status, the seeded service account key flagged (age in days is real; the threshold is 0 for the demo, say so), spend by project - including what this demo's own agents consumed.
- 2:00 Actions list. Cost table. "Governance of the model provider with the same read-only, scheduled, gated agents. Same query library conventions, same findings shape, same audit log."
- 2:30 Close: always-on, read-heavy, gated. Point at the audit log one last time: every action in the last half hour is a SQL statement in that file, and exactly one of them was a mutation.

## Fallback trigger

No admin key, provider errors, or the run passes 2 minutes: `--fallback`.
