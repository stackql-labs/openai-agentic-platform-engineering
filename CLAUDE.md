# CLAUDE.md

Project context and working rules for `openai-agentic-platform-engineering`.

## What this project is

A live demo for an OpenAI audience showing always-on, non-chat, non-coding-agent agentic use cases in platform engineering, SRE, audit (security posture and entitlements), and FinOps. Built on the OpenAI Agents SDK with StackQL as the tool surface via its MCP server.

The commercial message the demo must carry: these workloads run on schedules and events (tokens flow without a human initiating), are read-heavy (control plane metadata only - no customer data, no customer-facing surface), scale with estate size rather than seats, and gate the only dangerous step behind explicit approval. That profile survives enterprise security review where customer-facing agentic use cases stall.

Every scenario must visibly exhibit three properties:

1. Always-on - runs on a schedule or event trigger, not human-initiated
2. Read-heavy - SELECTs against cloud/SaaS control planes only
3. Gated mutation - any write requires approval, and every action is a logged SQL statement (deterministic, reviewable, replayable)

## Architecture

- OpenAI Agents SDK (Python) with the StackQL MCP server attached as a tool
- Model tiering: a mini-tier model runs the scheduled sweep/classification loop; flagged findings escalate to a frontier model for reasoning and remediation planning. Model IDs live in `.env`/config only - never hardcoded, they will change
- Structured outputs (JSON schema) for findings -> GitHub issue/PR creation
- StackQL materializes state snapshots into a local backend (SQLite or Postgres) each sweep; the drift agent briefs on deltas only, not the full estate
- Show run cost and the agent trace at the end of every scenario - the consumption economics stay in frame throughout the demo

### StackQL MCP tool surface

The MCP server exposes discovery tools (`list_providers`, `list_services`, `list_resources`, `list_methods`, `describe_resource`, `describe_method`), execution tools (`run_select_query`, `run_mutation_query`, `run_lifecycle_operation`, `validate_select_query`), and the curated query library (`query_library_search`, `query_library_get`). Verify the exact surface with `server_info` - names can vary by version.

Rules for agent code:

- Agents may call `run_select_query` freely
- `run_mutation_query` and `run_lifecycle_operation` are only reachable through the approval-gated path in `agents/triage/` - nowhere else
- Use `validate_select_query` in tests for every committed query

## Repo layout

```
openai-agentic-platform-engineering/
  CLAUDE.md
  WORK_ORDER.md
  README.md
  .env.example          # every required credential and model ID, documented
  seed/                 # creates the deliberately misconfigured demo estate
    aws/ azure/ gcp/ github/ idp/ openai/
    terraform/          # small TF-managed subset, exists to produce tfstate for the drift scenario
  agents/
    common/             # MCP wiring, model tiers, findings schemas, cost/trace helpers
    sweeps/             # cspm, entitlements, finops (read-only, scheduled)
    triage/             # closed-loop incident scenario with approval gate
    drift/              # snapshot, delta diff, tfstate comparison
    openai_estate/      # the closer - governing the OpenAI org itself
  queries/              # canonical StackQL SQL, one file per query, header comment: scenario, providers, expected columns
  runbooks/             # per-scenario demo scripts with timings and talk track
  fallbacks/            # canned results for every scenario (slow/failed live API path)
  snapshots/            # materialized state between runs (gitignored)
```

## Commands

Populate as built, keep current:

- `make setup` - install deps (uv), pull StackQL providers, verify MCP server with `server_info`
- `make seed` / `make teardown` - build and destroy the demo estate (teardown is idempotent, tag-filtered)
- `make sweep-<scenario>` - run one sweep end to end
- `make validate-queries` - run `validate_select_query` across `queries/`
- `make rehearse` - full demo run against fallbacks, no live APIs

## Guardrails - non-negotiable

- All demo cloud resources live in dedicated demo accounts/subscriptions/projects, never shared tenancy. Everything is tagged/labeled `purpose=oape-demo`
- Every mutation asserts the demo tag on its target before executing; teardown filters on the tag
- The one live mutation in the demo (triage scenario) must be small, reversible, and rehearsed
- Never commit secrets. Provider auth via env vars per StackQL conventions; `.env.example` documents the full set
- Never hardcode model IDs
- Query only demo tenancy - no queries against any other account, ever, including "just to check something"

## Conventions

- Python 3.12+, uv for environment management, ruff for lint/format
- All SQL lives in `queries/` - agents load queries from files, no inline SQL strings in agent code
- Findings schema is shared in `agents/common/` - all sweeps emit the same shape (provider, resource, finding_type, severity, evidence, proposed_remediation)
- Every scenario ships with a fallback in `fallbacks/` and a runbook in `runbooks/` before it counts as done
- Copy and narrative text: matter of fact, no hyperbole, technically precise about what the tools do (e.g. "cloud as data sources accessed via SQL", not "cloud as SQL tables")
- Punctuation in all authored text: use `-` not em dashes, `->` not arrow glyphs

## Definition of done for a scenario

1. Seed script produces its findings deterministically
2. Queries validated and committed with header comments
3. Agent produces structured findings and the downstream artifact (issue, PR, or brief)
4. Runbook written with timings, talk track beats, and the fallback path
5. Run cost and trace surfaced at the end of the run
6. Teardown leaves no residue

## What not to build

- No web UI beyond what the Agents SDK/platform tooling already provides - terminal, traces, and GitHub artifacts are the demo surface
- No chat interface - the point is that these agents are not chat
- No speculative scenarios beyond WORK_ORDER.md scope without checking first
