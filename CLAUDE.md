# CLAUDE.md

Project context and working rules for `openai-agentic-platform-engineering`.

## What this project is

A set of self-contained demos of always-on, non-chat agentic use cases in platform engineering,
SRE, audit and FinOps. Every demo uses OpenAI models and the StackQL MCP server as the tool
surface, so cloud and SaaS providers are data sources accessed via SQL and every action the agent
takes is a readable SQL statement.

Every use case must visibly exhibit three properties:

1. Always-on - runs on a schedule or an event trigger, not from a human typing a prompt
2. Read-heavy - SELECTs against cloud/SaaS control planes only (metadata, never customer data)
3. Gated mutation - any write requires explicit approval, is executed by code (not the model) as a
   single statement rendered from a query file, and is logged

The demos deliberately span runtimes and acquisition channels for the MCP server so each one is a
copyable pattern: Python (PyPI `stackql-mcp-server`), TypeScript and JavaScript (npm
`@stackql/mcp-server`), Rust (crates.io `stackql-mcp`, embedded in the binary).

## Repo layout

```
README.md            table of contents: one section per use case (description, intent, implementation, usage)
.env.example         every variable for every use case, documented
pricing.json         USD per 1M tokens by model id, for the cost line at the end of each run
Makefile             thin wrapper over each use case's own toolchain
finops/              Python + OpenAI Agents SDK  - FinOps / cost optimisation audit (aws, azure, google)
entitlements/        TypeScript + @openai/agents  - entitlements audit (entra_id or okta, aws, azure, google, github)
sre/                 Rust + async-openai          - agentic SRE with an approval gate (k8s on kind)
drift/               JavaScript + openai SDK      - drift detection over local snapshots (aws, azure)
edge/                Python + OpenAI Agents SDK  - edge autopilot, the edgepilot pattern (cloudflare, github)
```

Each use case directory holds its own manifest (pyproject / package.json / Cargo.toml), the agent
code with `setup` and `run` subcommands, `prompts/` (one markdown file per agent role, plus
`discovery.md`, the shared briefing on how to discover resources through StackQL), `queries/`
(a few example SELECTs under `examples/` and the code-owned statements the model never sees:
gate target assertions, drift snapshot sources, perturb and restore statements) and `stack/`
(a stackql-deploy stack that provisions the small demo estate the use case needs, per provider
where the use case is multi-cloud).

## Intent-driven, self-discovering agents

The point of every demo is StackQL's self-discovery surface. Agents are given an intent in prose,
not a query pack:

- Prompts live in `<use case>/prompts/*.md`, are loaded at run time, and have `{{ placeholders }}`
  for tenancy and policy values from `.env`. They state the intent, the scope, the output contract
  and the guardrails. They do not enumerate resource names, columns or SQL
- The agent discovers the API surface itself: `query_library_search` with its intent first, then
  `list_services` -> `list_resources` -> `describe_resource` / `list_methods` / `describe_method`,
  then `validate_select_query`, then `run_select_query`. The server's own guidance, the MCP
  resource `stackql://docs/instructions`, is read at startup and appended to the discovery briefing
- A mutation's IO contract is discovered the same way (`describe_method` on the write method);
  the model puts the exact single statement in its structured output and code decides whether to
  run it: allowlist of verb and resource, target assertion, approval phrase, one execution
- A handful of example queries may be cited from `queries/examples/` as the shape of a query, never
  as a pack to run

## StackQL MCP tool surface (v0.12.718, verify with `server_info`)

Discovery: `list_providers`, `list_registry`, `list_services`, `list_resources`, `list_methods`,
`describe_resource`, `describe_method`. Execution: `run_select_query`, `validate_select_query`,
`run_mutation_query`, `run_lifecycle_operation`. Library: `query_library_search`,
`query_library_get`. Admin: `server_info`, `pull_provider`, `reload_credentials`.

Rules for agent code:

- Model-facing servers run in `read_only` mode with `run_mutation_query`,
  `run_lifecycle_operation`, `pull_provider` and `reload_credentials` filtered out of the tool list
- `run_mutation_query` is reachable only from the approval-gated executor of a use case (the gate
  module), never from a model
- `validate_select_query` must pass for every committed SELECT

## Commands

- `make setup` - install dependencies per runtime, pull providers, verify `server_info`
- `make stack-<use case>` / `make teardown-<use case>` - build and destroy the demo estate
- `make <use case>` - run once (each README shows the always-on trigger)
- `make test` - offline tests, lint and typecheck across the use cases

## Guardrails - non-negotiable

- All demo resources live in dedicated demo accounts/subscriptions/projects/zones/clusters, never
  shared tenancy. Everything is named with `DEMO_PREFIX` and tagged/labelled
  `DEMO_TAG_KEY=DEMO_TAG_VALUE`
- Every mutation asserts the demo tag (or demo namespace/prefix) on its target before executing;
  stack teardown removes only what the stack created
- Never commit secrets. Provider auth via env vars per StackQL conventions; `.env.example`
  documents the full set
- Never hardcode model ids, account ids, subscription ids, project ids or org ids - they come from
  `.env` (`SWEEP_MODEL`, `REASONING_MODEL`, tenancy ids)
- Query only demo tenancy

## Conventions

- Python 3.12+ with uv and ruff; Node 22 with TypeScript strict; Rust 2021 edition with clippy
- No SQL string literals in agent code and no SQL in prompts beyond a cited example; the only SQL
  files are `<use case>/queries/examples/` and the code-owned statements listed above
- Findings shape is the same across runtimes: provider, resource, finding_type, severity, title,
  evidence, proposed_remediation, query_id, optional monthly_cost_estimate_usd
- Every run ends with the cost/trace block: tokens per step, USD estimate from `pricing.json`,
  trace URL or response ids, audit log path
- Copy and narrative text: matter of fact, no hyperbole, technically precise about what the tools
  do (e.g. "cloud and SaaS providers as data sources accessed via SQL")
- Punctuation in all authored text: `-` not em dashes, `->` not arrow glyphs, plain ASCII

## Definition of done for a use case

1. The stack builds and tears down cleanly and produces the use case's findings deterministically
2. Prompts in `prompts/` state intent, scope and guardrails; example and code-owned queries pass
   `validate_select_query` and carry header comments
3. The program produces structured findings and the downstream artifact (report, decision record,
   close-out note) and prints the cost/trace block
4. README section in the shared template (description, intent, implementation, usage)
5. Offline tests cover the prompt loader, the findings schema and, where there is a gate, that the
   mutation path is unreachable without a matching approval and that the statement allowlist
   rejects anything but the one permitted verb and resource

## What not to build

- No web UI, no chat interface - terminal output, traces and files are the surface
- No speculative use cases beyond the five listed without checking first
- No scheduler daemon - the README shows the cron/event trigger for each runtime
