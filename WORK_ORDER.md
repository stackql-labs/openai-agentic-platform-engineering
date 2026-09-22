# WORK_ORDER.md

Build order for `openai-agentic-platform-engineering`. Read CLAUDE.md first - its guardrails and conventions apply to every phase. Work the phases in order; each phase has acceptance criteria that must pass before moving on. Ask before deviating from scope.

## Objective

Deliver a rehearsable live demo (target 25-30 minutes) with five scenarios plus a closer, proving that platform engineering, SRE, audit, and FinOps agents are always-on, read-heavy, approval-gated workloads built on the OpenAI Agents SDK with StackQL via MCP.

Demo running order: quick universal-interface hook -> CSPM sweep -> entitlements audit -> gated incident triage (the one mutation) -> FinOps sweep -> drift briefing -> OpenAI estate closer.

## Phase 0 - scaffolding and environment

- Initialize the repo per the layout in CLAUDE.md, with `.env.example`, Makefile, uv project, ruff config
- Install StackQL and the StackQL MCP server; verify with `server_info` and record the tool surface in a comment in `agents/common/mcp.py`
- Pull providers: `aws`, `azure`, `google`, `github`, `openai`, plus the chosen IdP provider (see Phase 1)
- Wire a minimal Agents SDK agent to the MCP server and confirm it can run `SELECT` discovery queries end to end
- Implement model tiering config: `SWEEP_MODEL` and `REASONING_MODEL` env vars, a helper in `agents/common/` that constructs agents per tier
- Implement the shared findings schema and a cost/trace summary helper

Acceptance: `make setup` passes on a clean machine; a smoke agent lists providers and runs one SELECT per authenticated provider.

## Phase 1 - seed the demo estate

Create seed and teardown scripts per provider. Everything tagged/labeled `purpose=oape-demo`. Teardown idempotent, tag-filtered, verified to leave nothing behind.

- AWS: public S3 bucket; security group open to 0.0.0.0/0 on 22; IAM user with `AdministratorAccess` and an access key (note real age in runbook narrative); unattached EBS volume; unassociated EIP; unencrypted RDS instance (smallest class); one region without CloudTrail
- Azure: storage account with public blob access; NSG any/any rule; Owner role assignment at subscription scope; unattached managed disk; unassociated public IP
- GCP: bucket granted to `allUsers`; firewall rule 0.0.0.0/0; `roles/owner` binding on a service account; unattached persistent disk
- GitHub (demo org): repo without branch protection; stale deploy key; outside collaborator with admin
- IdP: Okta developer tenant preferred, Entra acceptable - decide based on available tenancy and record the choice here. Seed one leaver account still present in a privileged group, and a user set that mostly overlaps the GitHub org (for the join)
- OpenAI org: two throwaway projects and a service account API key (runbook narrative handles key age - flag threshold set low for demo purposes)
- `seed/terraform/`: a small TF-managed subset (a few tagged instances/buckets) applied once to produce tfstate, then manually perturbed (tag change, one attribute drift) so the drift scenario has real material

Prefer stackql-deploy for seeding where practical (dogfooding); plain scripts are acceptable where it is not. The Terraform subset must be Terraform - the tfstate is the point.

Acceptance: `make seed` then `make teardown` runs clean twice in a row; after seed, a checklist query per misconfiguration returns the expected row.

## Phase 2 - query library

Write and validate every StackQL query in `queries/`, one file per query, header comment per CLAUDE.md. Cover:

- All Phase 1 misconfigurations (detection query each)
- Cross-provider showpieces: unencrypted volumes joined to internet-facing instances; all privileged principals across AWS + Azure + GCP in one result set; GitHub org members LEFT JOIN IdP users -> orphans and leavers
- FinOps: unattached volumes/disks across all three clouds, unassociated EIPs/public IPs, stale snapshots, with a monthly cost estimate column where derivable
- Triage: instance/ASG health, LB target health, recent deployments (GitHub), the verification query for post-remediation
- Drift: snapshot materialization statements into the local backend; delta query between two snapshots; tfstate comparison using terraform_state_reader output
- OpenAI estate: projects, service account keys with created dates, usage/spend by project where the API exposes it

Acceptance: `make validate-queries` green; each cross-provider join returns correct results against the seeded estate; every query file's expected columns match reality.

## Phase 3 - sweep agents (CSPM, entitlements, FinOps)

Three scheduled, read-only agents in `agents/sweeps/`, all on the same pattern:

- Mini-tier model runs the sweep: executes the scenario's queries via MCP, classifies results against the findings schema
- Findings above a severity threshold escalate to the frontier model, which reasons about materiality (correlating across findings where relevant) and drafts remediation
- Output: structured findings -> a GitHub issue per material finding (CSPM, FinOps) and a recertification-style summary report (entitlements), plus a console brief
- Each run ends with the cost/trace summary
- Runnable ad hoc via make target and on a schedule (simple scheduler or cron wrapper is fine - the demo shows one live run and points at the schedule)

Acceptance: each sweep run against the seeded estate finds all planted misconfigurations, opens correctly formed issues, and prints cost/trace. A run against a clean estate produces no findings and no issues.

## Phase 4 - gated incident triage (the closed loop)

The one scenario that mutates. In `agents/triage/`:

- Trigger: synthetic alert (script or webhook stub) indicating a service symptom in the demo estate
- Diagnose: frontier model investigates with SELECTs only - instance/ASG state, LB target health, recent GitHub deployments
- Propose: structured remediation proposal naming the exact mutation (keep it small and reversible - scale out by one, restart an instance, or revert a tag)
- Gate: explicit human approval step in the terminal; on approval, the mutation executes via `run_mutation_query`/`run_lifecycle_operation`, asserting the demo tag on the target first
- Verify: post-mutation SELECT confirms recovery; agent closes the loop with a summary

Acceptance: full loop runs in under 5 minutes live; declining the gate cleanly aborts with no mutation; the mutation path is provably unreachable without approval (test this).

## Phase 5 - drift briefing

In `agents/drift/`:

- Snapshot job materializes the estate into the local backend per run
- Delta agent diffs current vs prior snapshot and briefs only on changes, classifying each as benign (tag churn) or material (security-relevant), with a one-paragraph plain-language brief
- tfstate comparison: parse `seed/terraform/` state via terraform_state_reader, compare to live, report the perturbations planted in Phase 1
- Talk track hook for the runbook: reasoning on deltas rather than the full estate is what makes hourly runs economical

Acceptance: with no changes between snapshots, the brief says so in one line and the run cost is visibly small; planted perturbations are detected and correctly classified.

## Phase 6 - OpenAI estate closer

In `agents/openai_estate/`: a short sweep over the OpenAI org itself - projects, service account key age against a threshold, spend/usage by project where exposed. Output is a one-page governance brief. This is the demo closer; keep it to 2-3 minutes of material.

Acceptance: runs against the seeded org and flags the planted key and projects.

## Phase 7 - runbooks, fallbacks, rehearsal

- Runbook per scenario in `runbooks/`: setup state required, exact commands, timings, talk track beats (including where to show the trace and cost), and the fallback trigger point
- Canned fallback per scenario in `fallbacks/`: recorded outputs replayable without live APIs, wired into `make rehearse`
- Master runbook: full running order with cumulative timings targeting 25-30 minutes, pre-demo checklist (seed state verified, credentials fresh, fallbacks tested), and a reset procedure between rehearsals
- README.md: what this is, setup, and the demo running order - written for a colleague to run the demo cold

Acceptance: `make rehearse` completes the full demo offline; one full live rehearsal completed against the seeded estate within the time target.

## Constraints and notes

- Model IDs, org IDs, account IDs: env/config only
- No scenario touches anything outside the demo tenancy - enforce, do not just document
- Narrative copy in runbooks follows CLAUDE.md tone rules: matter of fact, no hyperbole, technically precise
- If a provider API blocks a planned seed item (e.g. a resource that cannot be created in a demo tier), record the substitution in this file rather than silently changing scope

## Decisions and substitutions log

Recorded as built. Dates are absolute.

- 2026-09-22 - Package name: the OpenAI Agents SDK owns the top-level Python package `agents`, so the demo's agents live in `oape_agents/` (same internal layout as CLAUDE.md's `agents/`).
- 2026-09-22 - MCP server modes: `read_only` for every sweep, drift and estate agent (the server refuses all writes; verified with a direct client probe). The gated triage executor uses `full_access`. The server's `safe` mode needs an elicitation-capable MCP client and refuses writes from the Agents SDK client (verified), so the terminal approval gate in `oape_agents/triage/gate.py` is the elicitation step.
- 2026-09-22 - Model tier defaults in `.env.example`: `SWEEP_MODEL=gpt-5.4-mini`, `REASONING_MODEL=gpt-5.5`, chosen from the models the configured key can list. Prices in `config/pricing.yaml` from the OpenAI pricing page on this date.
- 2026-09-22 - Seeding mechanism per provider: AWS via boto3 (`seed/aws/`), Azure via a stackql-deploy stack (`seed/azure/`), GCP via StackQL INSERT/REPLACE/DELETE statements run through the stackql CLI (`seed/gcp/`), GitHub via the gh CLI as the org admin (`seed/github/`), Entra via Microsoft Graph (`seed/idp/`), OpenAI via the Administration API (`seed/openai/`). The Terraform subset is Terraform (`seed/terraform/`). Every mutation the demo agents can make goes through StackQL; the seed is not the demo surface.
- 2026-09-22 - AWS substitution: the demo account has account-level S3 Block Public Access enabled (all four settings), so a public bucket policy or ACL is refused (AccessDenied). The planted S3 finding is "bucket-level public access block present but fully disabled" (an absent configuration returns a per-bucket 404 and is not cleanly queryable); the reasoning tier is expected to correlate it with the account-level control when judging materiality.
- 2026-09-22 - AWS: "one region without CloudTrail" is planted as a single-region trail in the primary region; every other enabled region is uncovered. The account had no trails at all before seeding.
- 2026-09-22 - AWS triage estate: launch template + auto scaling group (min 1, desired 1, max 3) behind an internal application load balancer with a target group, tagged service=oape-checkout. The one live mutation is "scale out by one" (desired capacity 1 -> 2); `make triage-reset` puts it back.
- 2026-09-22 - Azure substitution: the demo service principal holds Contributor at subscription scope and cannot write role assignments, so no Owner assignment is planted. The detection query reports the Owner assignments that already exist at subscription scope (the humans who own the subscription). Grant the service principal User Access Administrator to plant a dedicated assignment for a managed identity instead.
- 2026-09-22 - GCP: networks, firewall rules and service accounts do not support labels; teardown filters those on the demo name prefix and a description carrying purpose=oape-demo. The firewall rule is on a dedicated custom-mode VPC with no subnets (zero blast radius). IAM policy reads through StackQL are flattened one row per binding, so the seed rebuilds the policy from rows before REPLACE.
- 2026-09-22 - GitHub substitution: no second GitHub user account is available in this tenancy (the `stackql` login is an organization), so the "outside collaborator with admin" finding is not planted. Set GITHUB_DEMO_COLLABORATOR and GITHUB_COLLABORATOR_TOKEN to plant it. The audit identity for the github provider is the org admin's token; the demo org is GITHUB_ORG.
- 2026-09-22 - GitHub teardown: the gh token lacks the delete_repo scope; teardown archives and renames demo repos when deletion is refused and prints `gh auth refresh -h github.com -s delete_repo`. Run that once to make teardown residue-free.
- 2026-09-22 - IdP choice: Microsoft Entra ID (the Okta developer tenant referenced by earlier demos no longer resolves). The app registration used by the azure/entra_id providers currently lacks Graph application permissions (User.ReadWrite.All, Group.ReadWrite.All, GroupMember.ReadWrite.All) - Graph returns 403. Until an admin grants them, `make seed` records the gap and the entitlements IdP join runs from fallbacks/.
- 2026-09-22 - OpenAI org: the configured key is a project key; the openai_admin provider and the seed need an Admin API key (OPENAI_ADMIN_KEY). Until one is added, the OpenAI seed is skipped and the closer runs from fallbacks/.
- 2026-09-22 - terraform_state_reader: not a StackQL feature (confirmed against the registry and docs). Implemented as `oape_agents/drift/terraform_state_reader.py`: parses the local tfstate into rows and loads them into the snapshot backend, so the tfstate-vs-live comparison is SQL over two tables.
- 2026-09-22 - StackQL query constraints found while validating the library (v0.12.718) and encoded in `oape_agents/common/tiers.py` for the models: one CTE level only; no subqueries in IN (...); WHERE predicates over json_each output of a live provider table are silently dropped (classification of unnested rows is a projected CASE column - `open_to_world`, `from_internet` - that consumers filter on); aggregates over json_each rows fail; a malformed JSON value aborts a json_each scan so sources are guarded with json_valid; base-column filters must sit inside the provider SELECT; double quotes inside LIKE patterns do not match; date windows use julianday(col) >= julianday('now', '-N days'); CAST AS INTEGER is not parsed (CAST AS REAL and ROUND are). Over a materialized view in the local backend none of these apply, which is why the drift path materializes first.
- 2026-09-22 - IAM queries sign against us-east-1 (`aws_iam_region`); `aws.iam.attached_user_policies` does not echo the user, so per-user queries are rendered once per user and UNION ALL-ed (`render_union`). The cross-cloud privileged-principals query receives the AWS admins as a literal UNION for the same reason.
- 2026-09-22 - Drift substitution: `aws.s3.bucket_versionings` maps get_bucket_versioning to an opaque `line_items` column, so bucket versioning is not a drift source. The Terraform perturbation is two changes (instance tag, security group ingress rule); bucket existence is snapshotted.
- 2026-09-22 - Sweep scope: AWS, Azure and GCP sweeps are account/subscription/project-wide, so pre-existing findings in the demo tenancy appear beside the planted ones (three more security groups open on 22, three more IAM users with admin keys, 25 old GCP snapshots). GitHub per-repo checks are scoped to the demo prefix. Decide before the demo whether the real findings are part of the story.
- 2026-09-22 - Measured live runs on the seeded estate: hook 21 s / USD 0.006; CSPM 249 s / USD 0.36 (18 issues); entitlements 112 s / USD 0.17; triage 63 s / USD 0.15 (1 mutation, recovered in 15 s); FinOps 55 s / USD 0.05; drift 4 s / USD 0.002 (USD 0 when nothing changed). Fallbacks recorded from these runs except openai_estate, which is synthetic pending an admin key.
- 2026-09-22 - Teardown acceptance: `make teardown` run twice after a full seed. Run 1 deleted 14 AWS resources (RDS deletion waited to completion), the Azure resource group, 6 GCP resources, and the GitHub deploy key; run 2 was a no-op with zero errors. `terraform destroy` removed the 5 terraform-managed resources. A direct tag/name check afterwards found nothing in AWS, Azure or GCP. Residue: the two demo GitHub repos are archived and renamed (`*-archived-<ts>`) because the gh token lacks delete_repo; with that scope granted, teardown deletes them. The AWS Resource Groups Tagging API lags terminated instances and their volumes for a while, so the "still tagged" line teardown prints right after a run can list resources that EC2 no longer reports.
- 2026-09-22 - The estate was torn down at the end of the build to stop cost. `make seed`, `make tf-apply`, `make seed-check` (about 12 minutes) rebuild it before a live rehearsal; fallbacks/ carry the recorded runs meanwhile.
