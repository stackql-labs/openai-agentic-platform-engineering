# openai-agentic-platform-engineering

A live demo for an OpenAI audience: always-on, non-chat agents for platform engineering, SRE, audit and FinOps, built on the OpenAI Agents SDK with StackQL as the tool surface via its MCP server.

Six scenarios, one pattern. Each agent runs on a schedule or an event, reads cloud and SaaS control-plane metadata through one SQL interface, and the only mutation in the whole demo sits behind an explicit terminal approval. Every action is a logged SQL statement; every run ends with its cost and trace on screen.

| segment | what it shows | make target |
|---|---|---|
| hook | one agent, one MCP server, four cloud control planes as SQL | `make smoke` |
| CSPM sweep | 21 posture queries across AWS, Azure, GCP, GitHub; mini-tier classifies, frontier tier assesses; issues filed | `make sweep-cspm` |
| entitlements audit | privileged principals across three clouds in one result set; GitHub members joined to the IdP; recertification report | `make sweep-entitlements` |
| gated triage | synthetic alert; diagnose with SELECTs; propose; human approval; one `run_mutation_query`; verify; close | `make alert`, `make triage` |
| FinOps sweep | idle and orphaned resources with monthly estimates computed in SQL; one issue per provider | `make sweep-finops` |
| drift briefing | StackQL materializes snapshots into SQLite; delta and tfstate comparison are SQL; the model briefs on deltas only | `make drift` |
| OpenAI estate | the same sweep pointed at the OpenAI organization: projects, key age, spend | `make openai-estate` |

## Setup

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/), the [stackql](https://stackql.io) binary on PATH, Terraform, the AWS CLI and gh CLI (for seeding only), GNU make and bash (Git Bash on Windows).

```
cp .env.example .env      # fill in credentials, tenancy ids and the two model ids
make setup                # uv sync, pull providers into ./.stackql, verify the MCP server
make smoke                # one SELECT per configured provider through the agent
```

`.env.example` documents every variable. Model ids (`SWEEP_MODEL`, `REASONING_MODEL`) and tenancy ids live only there.

## Building the demo estate

```
make seed                 # plant the misconfigured estate, tagged purpose=oape-demo (about 10 min, RDS dominates)
make tf-apply             # the terraform-managed subset that produces tfstate
make seed-check           # one detection query per planted misconfiguration
make teardown             # idempotent, tag-filtered; run it twice to prove it
```

The seed is per provider (`seed/`): boto3 for AWS, a stackql-deploy stack for Azure, StackQL statements for GCP, the gh CLI for GitHub, Microsoft Graph for Entra ID, the Administration API for OpenAI. Two items need credentials this tenancy did not have when the repo was built (Entra Graph consent, an OpenAI admin key); `WORK_ORDER.md` records every substitution.

## Running the demo

`runbooks/00_master.md` is the running order with cumulative timings (target 25-30 minutes), the pre-demo checklist and the reset procedure. Each segment has its own runbook with exact commands, talk track beats, where the trace and cost appear, and the fallback trigger.

```
make rehearse             # the full running order against recorded fallbacks, no live APIs
make reset                # between rehearsals: close issues, scale the ASG back, restore terraform state, clear runs/
```

Every scenario takes `--fallback` (replay `fallbacks/<scenario>.json`) and `--record` (save the live run as the new fallback).

## Layout

```
oape_agents/       the agents (the SDK owns the name `agents`, hence the prefix)
  common/          config, MCP factory and tool surface, model tiers, findings schema, query loader, cost ledger
  sweeps/          cspm, entitlements, finops on one base
  triage/          alert, diagnose/propose/gate/verify loop, the gate (only mutation path)
  drift/           snapshot (materialized views), terraform_state_reader, brief
  openai_estate/   the closer
queries/           all SQL, one file per query, header comment with id, providers, params, expected columns
seed/              per-provider seed and teardown, terraform subset, checklist
runbooks/          per-segment scripts and the master
fallbacks/         recorded runs for offline rehearsal
tests/             gate unreachability, query library contract, findings schema
```

## Guardrails

Read-only MCP servers for every agent except the triage executor; the mutation tools are filtered out of the model's tool list and refused by the server. The one executable statement is rendered from `queries/triage/aws_asg_scale_out.sql`, executed only after the operator types the approval phrase and after a SELECT asserts the demo tag on the target. `tests/test_gate.py` proves it. Queries pin the demo account, subscription, project and org from `.env`; teardown filters on the tag.

See `CLAUDE.md` for the working rules and `WORK_ORDER.md` for the build phases and the decisions log.
