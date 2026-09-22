# Master runbook

Running order, cumulative timings, the pre-demo checklist and the reset procedure. Each segment has its own runbook with the exact commands, talk track beats and fallback trigger.

Target: 25-30 minutes. The thesis to land in every segment: these agents run on a schedule or an event, they read control-plane metadata through one SQL interface, and the one dangerous step is gated behind a person. Cost and the trace stay on screen at the end of every run.

## Running order

| # | segment | runbook | live command | budget | cumulative |
|---|---|---|---|---|---|
| 1 | universal-interface hook | 01_hook.md | `make smoke` | 2 min | 2 |
| 2 | CSPM sweep | 02_cspm.md | `make sweep-cspm` | 6 min | 8 |
| 3 | entitlements audit | 03_entitlements.md | `make sweep-entitlements` | 4 min | 12 |
| 4 | gated incident triage | 04_triage.md | `make alert` then `make triage` | 6 min | 18 |
| 5 | FinOps sweep | 05_finops.md | `make sweep-finops` | 4 min | 22 |
| 6 | drift briefing | 06_drift.md | `make drift` | 4 min | 26 |
| 7 | OpenAI estate closer | 07_openai_estate.md | `make openai-estate` | 3 min | 29 |

Measured live durations on the seeded estate (2026-09-22): hook 21 s, CSPM 306 s before the reasoning-tier budget cap, entitlements 112 s, triage 63 s, FinOps 55 s, drift under 30 s, closer replayed. Start the CSPM run and talk over it; every other segment finishes inside its slot.

## Pre-demo checklist (the day before, then again 30 minutes before)

1. `make seed-status` shows the AWS, Azure, GCP and GitHub resources present; `make seed-check` passes every check except the two documented gaps (Entra Graph consent, OpenAI admin key).
2. `make tf-apply` has been run once and `make tf-perturb` has been applied, so the drift segment has material. `make drift` run once beforehand so a prior snapshot exists.
3. `.env` credentials are fresh: `make smoke` completes with four providers. The GitHub token is the org admin's; the gh CLI is logged in.
4. `make test` is green (16 tests, including the gate unreachability proofs).
5. `make rehearse --no-pause` completes offline; each `fallbacks/<scenario>.json` is dated and readable.
6. Terminal at 140 columns or wider so the cost table does not wrap. Browser tabs: the OpenAI traces dashboard (platform.openai.com/traces), the GitHub findings repo issues page, `runs/stackql-mcp-audit.jsonl` open in an editor.
7. The checkout ASG is at desired=1 with one healthy target (`make seed-status`). Run `make triage-reset` if not.
8. `make reset` has closed the previous rehearsal's issues and cleared `runs/`.

## Fallback policy

Every segment takes `--fallback`, which replays `fallbacks/<scenario>.json` with the recorded findings, assessments, artifacts and cost table marked "(replayed)". Trigger it when a live run has not produced its first tool call within 60 seconds, when a provider returns repeated errors, or when the segment is 2 minutes over budget. Say what happened: "that's the recorded run from this morning" - the audience is engineers.

## Reset between rehearsals

```
make reset            # closes demo issues, scales the ASG back to 1, restores the terraform subset, clears runs/
make tf-perturb       # re-plant the drift material
make drift            # take the "before" snapshot again (so the demo shows exactly one delta set)
make alert            # refresh the alert timestamp
```

Full rebuild when something is broken: `make teardown` then `make seed` (about 10 minutes, dominated by RDS) then `make tf-apply`, `make tf-perturb`, `make seed-check`.

## What the audience sees per segment

- the command, not a prompt
- the query pack or the statement about to run
- the findings table and the artifact link
- the cost table with both model tiers and the count of SELECT versus mutation tool calls
- the trace URL and the audit log line for the run

Copy in every runbook follows CLAUDE.md: matter of fact, technically precise, no hyperbole.
