# openai-agentic-platform-engineering

Always-on, non-chat agentic use cases for platform engineering, SRE, audit and FinOps, built on
OpenAI models with the StackQL MCP server as the tool surface. Cloud and SaaS providers are data
sources accessed via SQL, so every step an agent takes is a readable SQL statement: SELECTs for the
read-heavy work, and one approval-gated mutation where a use case needs to act.

Five use cases, each self-contained in its own directory with its own runtime, its own queries and a
stackql-deploy stack that provisions the small demo estate it needs.

| use case | directory | runtime | StackQL MCP package | providers |
|---|---|---|---|---|
| FinOps / cost optimisation audit | `finops/` | Python, OpenAI Agents SDK | PyPI `stackql-mcp-server` | aws, azure, google |
| Entitlements audit | `entitlements/` | TypeScript, `@openai/agents` | npm `@stackql/mcp-server` | entra_id (or okta), aws, azure, google, github |
| Agentic SRE | `sre/` | Rust, `async-openai` | crates.io `stackql-mcp` (embedded) | k8s (kind) |
| Drift detection | `drift/` | JavaScript, `openai` SDK | npm `@stackql/mcp-server` | aws, azure |
| Edge autopilot | `edge/` | Python, OpenAI Agents SDK | PyPI `stackql-mcp-server` | cloudflare, github |

Every use case follows the same pattern:

- runs on a schedule or an event, not from a human typing a prompt
- is given an intent in prose (`<use case>/prompts/*.md`), not a query pack: the agent discovers
  the resources and their IO contracts itself through the StackQL discovery tools
  (`query_library_search`, `list_services`, `list_resources`, `describe_resource`,
  `list_methods`, `describe_method`) and validates a statement before running it
- reads control-plane metadata with SELECTs through a StackQL MCP server started in `read_only`
  mode, with the mutation tools filtered out of the model's tool list
- where it acts, the model discovers the mutation's contract and proposes the exact statement;
  code checks it against an allowlist of verb and resource, asserts the target carries the demo
  tag, shows it to an operator, and runs it once after the exact approval phrase is typed
- uses two model tiers from `.env`: `SWEEP_MODEL` for the scheduled loop, `REASONING_MODEL` for
  escalation and remediation planning
- ends by printing token usage, an estimated cost (from `pricing.json`) and the trace or response ids

## Setup

Prerequisites: an OpenAI API key; credentials for the providers you want to run against (each use
case lists its own); [uv](https://docs.astral.sh/uv/) for the Python use cases, Node 22 for the
TypeScript and JavaScript ones, a Rust toolchain for `sre/`; `stackql-deploy` for the demo estates
(`pip install stackql-deploy`, or `curl -fsSL https://get-stackql-deploy.io/install.sh | sh`);
`kind` and `kubectl` for `sre/`. The StackQL MCP server itself is a dependency of each use case
(PyPI, npm or crates.io) and downloads the signed `stackql` binary on first run, so nothing else
is installed by hand.

```
cp .env.example .env      # credentials, tenancy ids, the two model ids - all documented inline
make setup                # per runtime: install deps, pull the StackQL providers, print server_info
make stack-<use case>     # provision that use case's demo estate with stackql-deploy
make <use case>           # run it once; each section below shows the always-on trigger
make teardown-<use case>  # remove the estate
```

`make setup-<use case>` and the per-directory commands in each section work on their own, so one use
case can be run without installing the toolchains for the others.

## FinOps / cost optimisation audit

**description:**
A scheduled, read-only sweep that finds idle and orphaned resources across AWS, Azure and Google Cloud: block storage attached to nothing, public addresses associated with nothing, and snapshots older than a threshold. The agents work from intent, not from a query pack: a mini-tier model discovers the resources and columns that express those states per provider through the StackQL query library and describe tools, runs the SELECTs it validated, and classifies every row into the shared findings shape with a list-price monthly estimate (labelled as an estimate, not billing data). A reasoning-tier model then decides, per provider, what is safe to delete now, discovers the delete or release contract with `describe_method`, and drafts a batch of StackQL DELETE/EXEC statements ordered by savings, for human review only. It produces a console table and brief, `runs/finops-<ts>.json`, a markdown cost report `runs/finops-<ts>.md`, and a cost/trace block.

**intent:**
The workload runs on a timer and costs tokens in proportion to estate size, not seats: nobody types a prompt, and every step is a SELECT against a cloud control plane (metadata only). The StackQL MCP server is started in `read_only` mode and the mutation tools are filtered out of both models' tool lists, so even a prompt-injected DELETE cannot execute; the statements the reasoning tier drafts are reviewable, replayable SQL that an engineer runs, or not. Two model tiers keep the recurring cost on the mini model and spend frontier-model tokens only on the judgement step, which is the profile that gets through an enterprise security review.

**implementation:**
- Python 3.12, uv, `openai-agents` (Agents SDK: `Agent`, `Runner`, `MCPServerStdio`, structured outputs, tracing).
- StackQL MCP server from PyPI `stackql-mcp-server`, a dependency of `finops/pyproject.toml`; its `stackql-mcp` console script is launched from the project venv by `MCPServerStdio` as a stdio subprocess (the signed `stackql` binary is downloaded on first run). Server mode `read_only` with an audit file (`STACKQL_MCP_AUDIT_LOG`); a static tool filter hides `run_mutation_query`, `run_lifecycle_operation`, `pull_provider` and `reload_credentials` from the models. There is no mutation path in this use case.
- Prompts, not queries: each agent's instructions are prose in `finops/prompts/` (`sweep.md`, `reasoning.md`), rendered by `finops/finops/prompts.py`, which substitutes `{{ placeholders }}` (tenancy and policy values such as `{{ tenancy }}`, `{{ snapshot_max_age_days }}`, `{{ demo_tag_key }}`) from code overrides, then env, then documented defaults, and fails naming the variable when one is missing. The prompts state the intent per provider (what waste, what counts as a finding), the tenancy the model may query, the output contract in words and the guardrails; they name no resources, columns or SQL.
- Self-discovery: `finops/prompts/discovery.md` is appended to both prompts and tells the model how to use the StackQL tool surface - `query_library_search` / `query_library_get` first, then `list_services`, `list_resources`, `describe_resource`, `list_methods` and `describe_method` for the resource, its required parameters and columns, `validate_select_query` before `run_select_query` - plus the few dialect facts that matter (provider.service.resource naming, required params in WHERE, JSON via `json_extract`/`json_each`, zero rows is an answer, no CTEs in validation). At startup the program reads the server's own instructions (MCP resource `stackql://docs/instructions`) through the Agents SDK server and appends them to that briefing; if the read fails the run continues and says so.
- Model tiers from `.env`: `SWEEP_MODEL` discovers, runs and classifies (output type `FindingSet`, with the model's list-price estimate per row); `REASONING_MODEL` receives all findings batched per provider, discovers the delete/release method contract and returns a `ProviderPlan` (proposed statements with reasons, deferred items with reasons). Model ids are never in code.
- Queries: `finops/queries/examples/*.sql` hold three illustrative SELECTs (one per provider, with the usual header: id, providers, params, expected_columns, description) as examples of the shape of a finops query; the models never receive them and `finops validate` checks them with `validate_select_query`. No SQL is code-owned in this use case: there is no gate, so nothing is rendered from a file at run time.
- Stacks: `finops/stack/aws` (1 GB gp3 volume attached to nothing, unassociated Elastic IP), `finops/stack/azure` (resource group, 4 GB Standard_LRS disk attached to nothing, Standard static public IP associated with nothing), `finops/stack/google` (10 GB pd-standard disk attached to nothing). One stack per provider so one cloud is enough; everything is named with `DEMO_PREFIX` and tagged/labelled `DEMO_TAG_KEY=DEMO_TAG_VALUE`.
- Cost block from the repo-root `pricing.json` (`OPENAI_PRICING_JSON` extends it); trace URL from the Agents SDK trace id.

**usage:**
```
cd finops && uv sync                                        # deps incl. the MCP server package
uv run python -m finops setup                               # pull aws, azure, google; print server_info
uv run python -m finops validate                            # validate_select_query on queries/examples/
cd .. && stackql-deploy build finops/stack/aws dev --env-file .env      # one per provider you have
stackql-deploy build finops/stack/azure dev --env-file .env
stackql-deploy build finops/stack/google dev --env-file .env
cd finops && uv run python -m finops run                    # sweep every provider with credentials
uv run python -m finops run --providers aws,azure           # subset
uv run python -m finops run --skip-reasoning                # sweep tier only
uv run python -m finops run --snapshot-max-age-days 7       # override the stale threshold
```
`uv run finops <setup|validate|run> ...` is the same program via the console script.
Always-on: a crontab line runs it nightly (the Agents SDK trace and the run files are the record):
```
15 2 * * * cd /path/to/repo/finops && uv run python -m finops run >> ../runs/finops-cron.log 2>&1
```
or a GitHub Actions workflow with `on: schedule: [{cron: "15 2 * * *"}]` running the same command with the `.env` values as repository secrets.

Teardown: `stackql-deploy teardown finops/stack/<aws|azure|google> dev --env-file .env` from the repo root.

Expected output: a findings table (severity, provider, finding, resource, estimated USD/month) and a brief; per provider, the proposed statements with reasons and the deferred items; the cost/trace table shows the discovery and library calls alongside the SELECTs; the paths of `runs/finops-<ts>.md` and `runs/finops-<ts>.json`; then the cost/trace table (per step model, requests, input/cached/output/reasoning tokens, select vs mutation tool calls - always 0 mutation - USD), the trace URL and the audit log path.

## Entitlements audit

**description:**
A scheduled, read-only recertification sweep over five control planes: AWS, Azure, Google Cloud, GitHub and the identity provider (Microsoft Entra ID, or Okta with `IDP_PROVIDER=okta`). One StackQL MCP server process holds the federated credentials for all of them, and the agent is given four intents rather than queries: privileged principals across the three clouds in one result set, GitHub org members against the IdP (orphans and leavers), the privileged IdP group's members and their account state, and outside collaborators with admin on demo repos. It discovers the resources, parameters and columns for each through the StackQL discovery tools and the query library, joins across providers in SQL, and produces a console table and brief, a run record `runs/entitlements-<ts>.json`, and a recertification report `runs/entitlements-recertification-<ts>.md` with a confirm/revoke column, evidence and the removal statement per finding.

**intent:**
Always-on (cron-triggered, nobody types a prompt), read-heavy (SELECTs against control-plane metadata only, no customer data) and non-mutating: the report drafts the StackQL statement that would remove each grant, and this program never executes it. The demonstration is StackQL's self-discovery surface: the prompts state what question to answer and the model finds the API surface and IO contracts itself, so the same code audits a new provider or resource without a new query pack. The mini-tier model does the sweep and classification; only findings at or above `ESCALATION_SEVERITY` reach the frontier model. The model-facing server runs in `read_only` mode with the mutation and admin tools filtered out, which is what survives an enterprise security review.

**implementation:**
- TypeScript on Node 22, `@openai/agents` 0.18 with zod 4 schemas as the structured outputs (findings, assessments) and `withTrace` for the trace id; run with `tsx`, typechecked with `tsc`
- The agents work from intent prompts in `entitlements/prompts/`: `sweep.md` (the four questions, the tenancy in scope, the findings contract in words, guardrails), `reasoning.md` (materiality, confirmer, least-privilege alternative, the removal statement whose contract the model looks up with `list_methods` / `describe_method`, blast radius) and `discovery.md` (the shared briefing: query library first, then list/describe, then validate, then run; the few dialect facts that matter; a mutation is never executed by a model). Code substitutes `{{ placeholders }}` from the environment (`entitlements/src/prompts.ts`); a placeholder without a value fails naming the variable, and a provider without credentials renders as "not configured" so the model skips it
- At startup the program reads the server's own instructions resource `stackql://docs/instructions` through `MCPServerStdio.readResource` and appends the text to the discovery briefing, so the server's current guidance travels with the prompt; if the read fails the run continues and says so
- Resource discovery is the model's job: `query_library_search` / `query_library_get` for vetted templates, `list_services`, `list_resources`, `describe_resource`, `list_methods` and `describe_method` for everything else, including how to join across providers and how to fan out one SELECT per key (UNION ALL, key projected as a literal) when a method does not echo its key. No SQL lives in agent code and the agents hold no function tools; the StackQL MCP server is the whole tool surface
- `entitlements/queries/examples/` holds two illustrative SELECTs the sweep prompt cites as "the shape, not a pack": the flat cross-cloud `UNION ALL` of privileged principals and the GitHub-members-to-Entra-ID `LEFT JOIN`. They are the only committed SQL; this use case has no mutation path, so there is no code-owned statement
- StackQL MCP server from npm (`@stackql/mcp-server`, v0.12.718): the launcher in `node_modules` is spawned by `MCPServerStdio` with the full process environment, `--mcp.config` `{"server":{"mode":"read_only","audit":{"file":...}}}` and `createMCPToolStaticFilter` allowing only the discovery, `run_select_query`, `validate_select_query` and query-library tools; `run_mutation_query`, `run_lifecycle_operation`, `pull_provider` and `reload_credentials` are blocked (`entitlements/src/mcp.ts`)
- Two model tiers from the environment: `SWEEP_MODEL` (discovery and classification, `SWEEP_REASONING_EFFORT`) and `REASONING_MODEL` (assessment, `REASONING_REASONING_EFFORT`); no model id in code
- Stack: `entitlements/stack/aws` (IAM user `<DEMO_PREFIX>-admin` with AdministratorAccess) and `entitlements/stack/entra_id` (group `<DEMO_PREFIX>-cloud-admins` with a disabled member `<DEMO_PREFIX>-leaver`); no Azure or GCP writes - see `entitlements/stack/README.md`
- Every run ends with the cost and trace block: per tier the requests, input/cached/output/reasoning tokens, tool calls (select vs mutation), USD from `pricing.json` (`OPENAI_PRICING_JSON` overrides), the trace URL and the audit log path

**usage:**
```
cd entitlements && npm install
npm run setup                     # pull_provider for aws, azure, google, github and the IdP; prints server_info
npm run validate                  # validate_select_query over the two examples in entitlements/queries/examples/
stackql-deploy build entitlements/stack/aws dev --env-file .env        # from the repo root
stackql-deploy build entitlements/stack/entra_id dev --env-file .env
npm run sweep                     # one run: table + brief, runs/entitlements-<ts>.json, the recertification report, cost/trace
npm run sweep -- --dry-run        # print the rendered sweep instructions and the 11 tools the model sees, validate the examples, call no model
npm run sweep -- --idp okta       # same sweep against Okta instead of Entra ID
npm test                          # prompt loader, example loader, findings schema, read-only server invariant
stackql-deploy teardown entitlements/stack/entra_id dev --env-file .env
stackql-deploy teardown entitlements/stack/aws dev --env-file .env
```
Schedule it with cron (weekly recertification, Monday 06:10 UTC):
```
10 6 * * 1  cd /opt/openai-agentic-platform-engineering/entitlements && npm run sweep >> ../runs/entitlements-cron.log 2>&1
```
or with a GitHub Actions cron workflow that checks out the repo, runs `npm ci && npm run sweep` with the `.env` values as secrets, and uploads `runs/` as an artifact. Expected output: the discovery and SELECT tool calls in the trace, a findings table (critical: leaver still privileged; high: non-human principal with owner/admin, outside collaborator admin; medium: orphan member, human Owner at subscription scope), the reasoning tier's assessment of the escalated ones, the report path, and the cost/trace block.

## Agentic SRE - gated remediation on Kubernetes

**description:**
A synthetic alert (checkout p95 over SLO, request queue growing on the only ready replica) fires against a kind cluster; nobody types a prompt. The diagnose agent gets the alert and an intent prompt, discovers the k8s resources through the StackQL discovery tools and the query library, and reads capacity, pod health and Warning events with SELECTs. The propose agent discovers the scale mutation's IO contract (`list_methods` / `describe_method`) and writes the exact statement plus a verification SELECT into its structured output. Code checks that statement against an allowlist, stops at an approval gate, asserts the target is the demo deployment, executes the one statement, polls the verification SELECT until ready replicas match, and a mini-tier model writes the close-out note. Output: the terminal transcript, `runs/sre-<timestamp>-<id>.json`, and a statement log.

**intent:**
Closed-loop incident response where the model reasons and code acts: always-on (event trigger, not a chat), read-heavy (every diagnostic step is a SELECT against the Kubernetes control plane, no workload data), and the single mutation is a reviewable SQL statement that runs only after a human types the approval phrase and only against a target that carries the demo namespace, name prefix and label. Two model tiers keep the frontier model on diagnosis and planning and the mini tier on the write-up; the run ends with tokens, USD and response ids on screen. The model never holds a mutation tool, which is what an enterprise security review asks for first.

**implementation:**
- Rust (edition 2021), `async-openai` 0.42 on the Responses API: a function-calling loop whose tools are bridged from the MCP server's tool list, and structured outputs via `json_schema` text formats (`diagnosis`, `proposal`, `closeout`).
- crates.io `stackql-mcp` 0.12: the StackQL MCP server is embedded, started in-process over stdio. Default `sidecar` feature downloads the sha256-pinned binary into `~/.stackql/mcp-server-bin/` on first run (set `STACKQL_MCP_BIN` to reuse one); the crate's `vendored` feature embeds the `.mcpb` with `include_bundle!()` for a single shippable binary with no runtime download.
- Prompts, not query packs: each model step reads its instructions from `sre/prompts/` at run time (`diagnose.md`, `propose.md`, `closeout.md`, plus `discovery.md` appended to every model-facing prompt), with `{{ k8s_namespace }}`, `{{ kube_cluster_addr }}`, `{{ kube_protocol }}`, `{{ sre_target_deployment }}`, `{{ demo_prefix }}`, `{{ demo_tag_key }}` and `{{ demo_tag_value }}` substituted from `.env`. The prompts state intent, scope, output contract and guardrails in prose and name no resources, columns or SQL: the agent uses `query_library_search` / `query_library_get` first, then `list_services`, `list_resources`, `describe_resource`, `list_methods` and `describe_method` to find resources and their IO contracts, and `validate_select_query` before `run_select_query`. At startup the program reads the server's own MCP resource `stackql://docs/instructions` (rmcp `read_resource`) and appends it to the discovery briefing; if the read fails the run continues and says so. Edit a prompt file and rerun, no rebuild.
- Server modes: every model-facing server runs `read_only`, and `sre/src/mcp.rs` filters the tool list to `run_select_query`, `validate_select_query`, the two query library tools and the `list_*` / `describe_*` discovery tools. `sre/src/gate.rs` is the only module that starts a `full_access` server and the only caller of `run_mutation_query`; a unit test scans the source tree to keep it that way.
- Gate: the propose agent's structured output carries the exact statement it discovered the contract for. `gate.rs` accepts it only if it is a single statement with verb `UPDATE` or `REPLACE` on `k8s.apps.deployments_scale` or `k8s.apps.deployments`, with no other verb or `OR`, a WHERE pinning `namespace` = `K8S_NAMESPACE`, `name` = the target, `cluster_addr` and `protocol`, and a `"replicas": N` body equal to the proposed count; anything else is rejected before the operator sees it. A nonce is minted per proposal; an approval exists only from the exact phrase `approve <proposal-id>` (or `--approve`); `execute_approved` re-checks the nonce and the allowlist, runs the code-owned `sre/queries/assert_demo_target.sql` on the executor server (namespace = `K8S_NAMESPACE`, name starts with `DEMO_PREFIX` or equals `SRE_TARGET_DEPLOYMENT`, label `DEMO_TAG_KEY=DEMO_TAG_VALUE`), then sends exactly one statement. The model's verification SELECT is validated with `validate_select_query` and used for the poll when it returns `spec_replicas` and `ready_replicas`; otherwise the code-owned `sre/queries/verify_replicas.sql` is used. `--decline` proves the abort path with zero mutations.
- SQL that stays in the repository (`sre/queries/`, each with a parsed header: `id`, `providers`, `params`, `expected_columns`, `description`): code-owned `assert_demo_target.sql` (the gate's target assertion, never shown to a model), `verify_replicas.sql` (the fallback verification) and `reset_scale.sql` (the statement `sre run --reset` renders, since no model is involved there); and `queries/examples/` with two illustrative SELECTs (`deployment_state.sql`, `warning_events.sql`) the diagnose prompt cites as the shape of a query, not as a pack to run. Everything else is discovered at run time.
- Model tiers from `.env`: `REASONING_MODEL` diagnoses and proposes (with tools), `SWEEP_MODEL` writes the close-out (no tools). Cost from the repo-root `pricing.json` (`OPENAI_PRICING_JSON` overrides).
- The embedded crate launches the server with its own audit disabled, so the program writes its own statement log: one JSON line per tool call (mode, tool, SQL, result) at `STACKQL_MCP_AUDIT_LOG`.
- Stack: `sre/stack` (stackql-deploy, provider k8s) creates the namespace, a one-replica `<DEMO_PREFIX>-checkout` deployment (`hashicorp/http-echo`, 10m CPU / 16Mi requests) and a ClusterIP service, all labelled `purpose=agentic-demo`.

**usage:**
```
kind create cluster --name agentic-demo          # local cluster; no cloud credentials involved
kubectl proxy --port=8001 &                      # KUBE_CLUSTER_ADDR=localhost:8001, KUBE_PROTOCOL=http
cd sre && cargo build --release && ./target/release/sre setup    # pulls the k8s provider, prints server_info, reads stackql://docs/instructions, renders the prompts
cd .. && stackql-deploy build sre/stack dev --env-file .env      # namespace + deployment (1 replica) + service
cd sre && ./target/release/sre validate-queries  # validate_select_query over the code-owned and example SELECTs in sre/queries/ (needs the proxy up)

./target/release/sre alert                       # the event: writes runs/alert.json
./target/release/sre run                         # diagnose -> propose -> gate (type `approve <id>`) -> execute -> verify -> close-out
./target/release/sre run --approve               # unattended: approve at the gate without the prompt
./target/release/sre run --decline               # prove the abort path: zero mutations
./target/release/sre run --reset --approve       # scale back to 1 through the same gate (no model)

cd .. && stackql-deploy teardown sre/stack dev --env-file .env
kind delete cluster --name agentic-demo
```
Event trigger (the loop runs when an alert lands, not when someone types): point an Alertmanager `webhook_configs` receiver at anything that writes `runs/alert.json`, and let a file watch start the run:
```
inotifywait -m -e close_write runs/alert.json | while read -r _; do (cd sre && ./target/release/sre run --approve); done
```
Expected output: the alert, the discovery and SELECT calls of the diagnosis (one line per tool call) and its replica and pod numbers, the proposal with the discovered statement and verification SELECT, the allowlist-checked statement at the gate, the target assertion, one `run_mutation_query`, the verify poll reaching `ready=2`, the close-out paragraph, then the cost/trace block (per step tokens, USD, response ids, statement log and run record paths). Variables read (all from the repo-root `.env`): `OPENAI_API_KEY`, `SWEEP_MODEL`, `REASONING_MODEL`, `SWEEP_REASONING_EFFORT`, `REASONING_REASONING_EFFORT`, `OPENAI_PRICING_JSON`, `DEMO_PREFIX`, `DEMO_TAG_KEY`, `DEMO_TAG_VALUE`, `STACKQL_APPROOT`, `STACKQL_MCP_AUDIT_LOG`, `STACKQL_MCP_BIN`, `KUBE_CLUSTER_ADDR`, `KUBE_PROTOCOL`, `K8S_NAMESPACE`, and optionally `SRE_TARGET_DEPLOYMENT` (default `<DEMO_PREFIX>-checkout`), `SRE_APP_LABEL` (`checkout`), `SRE_VERIFY_TIMEOUT_SECS` (120), `SRE_VERIFY_POLL_SECS` (5).

## Drift detection

**description:**
An hourly job snapshots network control-plane state from AWS (security groups and EC2 instances in `AWS_REGION`) and Azure (network security groups in `AZURE_SUBSCRIPTION_ID`) through the StackQL MCP server, normalises each resource to a stable attribute JSON and writes it to a local sqlite database (`snapshots/drift.db`). The delta between the two latest snapshots is computed in plain SQL; only when there are deltas is a model called, and it sees the deltas only. The intended state is the stackql-deploy stack under `drift/stack/`; anything else is drift. It produces a classified change list plus a one-paragraph brief on the console and in `runs/drift-<ts>.json`, followed by the cost and trace block.

**intent:**
Always-on: the job runs from cron with no human in the loop, and a run with no deltas costs API calls only (no model call, USD 0.0000). Read-heavy: every statement the model can reach is a SELECT on a `read_only` server with the mutation and admin tools filtered out of its tool list; the model reasons over deltas, not the estate, so tokens scale with change, not with estate size. Gated mutation: the only write path is `perturb`, operator tooling that simulates the out-of-band change a person makes by clicking in a console - one rendered statement per provider, executed by code on a separately started `full_access` server after the demo tag is asserted and `approve perturb` is typed. The mini tier does the classification; nothing in this use case needs the frontier tier.

**implementation:**
- Plain JavaScript on Node 22 (ESM, no agent framework). The `openai` SDK Responses API with a `json_schema` structured output (`changes[]` classified `benign` or `material` with a reason, plus `brief`) and function tools bridged from the MCP server; the function-call loop runs in `drift/src/brief.js` and is bounded by `DRIFT_MAX_TOOL_CALLS`.
- The model works from intent prompts in `drift/prompts/`, not from SQL. `brief.md` states the question (classify each delta, cite ids), the scope (the region, subscription, prefix and tag from `.env`, filled into `{{ placeholders }}` by `drift/src/prompts.js`) and the guardrails; `discovery.md` is the shared briefing on the StackQL tool surface (query library first, then `list_services` -> `list_resources` -> `describe_resource` -> `list_methods` / `describe_method`, then `validate_select_query`, then `run_select_query`) with the few dialect facts that matter. At startup the client reads the MCP resource `stackql://docs/instructions` (`readResource`) and appends the server's own instructions; if the read fails the run continues and says so. When a delta is ambiguous the model discovers the resource and runs a targeted SELECT to add context; it never receives a query pack and there is no `queries/examples/` directory in this use case because the model has nothing to select routinely.
- SQL that stays code-owned in `drift/queries/` (header comments: id, providers, params, expected_columns, kind, description; placeholders filled from `.env`, `aws_region` -> `AWS_REGION`): the snapshot sources `snapshot_*.sql`, because a snapshot is a mechanical scheduled job with no model in it and the normalisers in `drift/src/normalize.js` depend on fixed columns; `delta.sql`, plain SQL over the local sqlite snapshots; and `perturb_*_target.sql` / the perturb mutations, which are operator tooling rendered and executed only by the gate. The model never sees any of them.
- MCP client from `@modelcontextprotocol/sdk` spawning the npm `@stackql/mcp-server` launcher (`node_modules/.bin/stackql-mcp`) over stdio with `--approot` and `--mcp.config` (`mode`, audit file). Modes: `read_only` for `setup`, `snapshot`, `run` and `validate`; `full_access` only in `drift/src/perturb.js`. `run_mutation_query`, `run_lifecycle_operation`, `pull_provider` and `reload_credentials` are never bridged to the model (`drift/src/mcp.js`, tested in `drift/test/bridge.test.js`).
- `node:sqlite` as the local backend: table `snapshot_<ts>` (provider, resource_type, resource_key, state_json, attrs_json) per snapshot plus a `snapshots` registry; `drift/queries/delta.sql` yields added / removed / changed rows by comparing `attrs_json`. Normalisers in `drift/src/normalize.js` (name, sorted ingress rule strings, tags).
- Model tier: `SWEEP_MODEL` at `SWEEP_REASONING_EFFORT`. Cost from `pricing.json` (override with `OPENAI_PRICING_JSON`); response ids are printed as the trace reference.
- Stack: `drift/stack/aws` (one security group `<DEMO_PREFIX>-app-sg` in the region's default VPC, ingress tcp/443 from 10.0.0.0/8) and `drift/stack/azure` (resource group `<DEMO_PREFIX>-drift-rg` with NSG `<DEMO_PREFIX>-app-nsg`, one inbound Allow tcp/443 from 10.0.0.0/8). Both tagged `DEMO_TAG_KEY=DEMO_TAG_VALUE`, no instances, no cost.
- Environment read from `.env`: `OPENAI_API_KEY`, `SWEEP_MODEL`, `SWEEP_REASONING_EFFORT` (low), `OPENAI_PRICING_JSON` (optional), `DEMO_PREFIX` (agentic-demo), `DEMO_TAG_KEY` (purpose), `DEMO_TAG_VALUE` (agentic-demo), `STACKQL_APPROOT` (~/.stackql), `STACKQL_MCP_AUDIT_LOG` (runs/stackql-mcp-audit.jsonl), `DRIFT_SNAPSHOT_DB` (snapshots/drift.db), `DRIFT_MAX_TOOL_CALLS` (6), `DRIFT_ROW_LIMIT` (5000), `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `AZURE_SUBSCRIPTION_ID`, `AZURE_LOCATION` (stack only).

**usage:**
```
cd drift && npm install && cd ..
node drift/src/cli.js setup                                   # pulls aws and azure, prints server_info and the tool list
stackql-deploy build drift/stack/aws dev --env-file .env      # intended state, AWS
stackql-deploy build drift/stack/azure dev --env-file .env    # intended state, Azure
node drift/src/cli.js snapshot                                # baseline snapshot: API calls only, no model call
node drift/src/cli.js run                                     # snapshot -> delta -> "no changes ... no model call made"
node drift/src/cli.js perturb                                 # operator: simulate the console click; type "approve perturb"
node drift/src/cli.js run                                     # snapshot -> 2 deltas -> SWEEP_MODEL classifies them material
node drift/src/cli.js perturb --restore --approve             # reverse it unattended (--decline proves the abort path)
node drift/src/cli.js run --no-snapshot                       # brief on the two latest snapshots without a new one
node drift/src/cli.js validate                                # validate_select_query for every code-owned SELECT and stack anchor
cd drift && npm test                                          # node:test - normaliser, delta SQL, query and prompt loaders, tool bridge, gate
stackql-deploy teardown drift/stack/aws dev --env-file .env
stackql-deploy teardown drift/stack/azure dev --env-file .env
```
Always-on trigger - one crontab line for an hourly run from the repo root (each run appends `runs/drift-<ts>.json`):
```
0 * * * * cd /path/to/openai-agentic-platform-engineering && node drift/src/cli.js run >> runs/drift-cron.log 2>&1
```
A quiet hour prints the snapshot row counts, `snapshot <ts> vs <prev>: 0 change(s)`, one line saying no model call was made, and a cost block totalling USD 0.0000 with only select tool calls. After `perturb`, the run prints the two `changed` rows (ingress `tcp:22-22:0.0.0.0/0` on the security group, rule `AllowAnyInbound` on the NSG), a line with the size of the server instructions appended to the prompt, any discovery tool calls the model made, its classification of each delta as `material` with a reason, the paragraph brief, and the cost block with the response ids. Node prints one `ExperimentalWarning` for `node:sqlite`; add `--no-warnings=ExperimentalWarning` to silence it.

## Edge autopilot

**description:**
Rebuilds the stackql/edgepilot pattern on OpenAI: a recon agent reads a Cloudflare zone's live HTTP analytics and its rate limit ruleset over the last N minutes, a decision agent judges whether traffic is elevated against a threshold policy and, when it is, proposes the single REPLACE statement that tightens the rule; the statement runs only after an allowlist check and explicit approval. Providers: cloudflare (recon and the gated change) and github (optional decision record as an issue). It produces a recon report, a structured decision with the proposed statement, an audited SQL statement, a decision record (`runs/edge-decisions.jsonl` or a GitHub issue) and a run file under `runs/`.

**intent:**
An always-on control loop that is not a chat: it fires from a schedule or an alert hook, spends almost all of its tokens on SELECTs against a control plane (traffic counts and rule config, never request bodies or customer data), and puts the one write behind a human phrase, a target assertion and a post-change verification. The agents are given intents, not SQL: they discover the resources and the IO contracts through the StackQL query library and discovery tools, which is the point of the demo. The cheap tier reads, the frontier tier reasons and proposes, and code, not the model, checks and executes the statement, so the whole run is reviewable as SQL in the audit log. The original published its decision to Confluent Kafka; StackQL has a confluent provider, so the same record could go to a topic, but this demo does not build that sink.

**implementation:**
- Python 3.12, uv, `openai-agents` (Agents SDK), `pydantic` structured outputs, `rich` console
- StackQL MCP server from PyPI (`stackql-mcp-server`): the `stackql-mcp` console script in `edge/.venv` is spawned by `MCPServerStdio`; every statement is echoed to the console as it runs
- prompts (`edge/prompts/`): one markdown file per agent role, prose, with `{{ placeholders }}` for the zone id, the window, the policy thresholds and the demo prefix, rendered by `edge/edge/prompts.py`. `recon.md` gives the intent (traffic in the window, the rate limit rule currently applied), `decide.md` gives the policy and asks for the exact change, `discovery.md` is the shared briefing on discovery (library first, then list/describe, then validate, then run) plus the dialect facts that matter. At startup the program reads the server's own guidance, the MCP resource `stackql://docs/instructions`, and appends it to the briefing; if the read fails the run continues and says so. No prompt names a resource, a column or a statement (a test enforces it)
- recon agent: `SWEEP_MODEL`, server in `read_only` mode with the mutation and admin tools filtered out; finds the analytics and the ruleset through `query_library_search` / `query_library_get` (the library covers both) or `list_resources` / `describe_resource`, runs the SELECTs and returns a `ReconReport`; code recomputes the arithmetic from the rows the tools returned and collects the statements and resources actually read from the tool calls
- decision agent: `REASONING_MODEL`, the same read-only server; returns `Decision {action: tighten|hold, new_threshold, rationale, risk, statement, verification_select, rollback_statement}`. On tighten it discovers the write contract itself (`list_methods` for the method whose verb is REPLACE, `describe_method` for the required params and the rules shape) and puts the exact statement in its output. The policy (`ELEVATED_RPS`, `TIGHTENED_THRESHOLD`, `BASELINE_THRESHOLD`) is enforced again in code; an override drops the model's statements
- gate and executor (`edge/edge/gate.py`, the only code that starts a `full_access` server, never given to a model): the allowlist accepts exactly one statement of the form `REPLACE` on `cloudflare.rulesets.phases`, single statement, no other verb or comment, WHERE pinned to `CLOUDFLARE_ZONE_ID` and the `http_ratelimit` phase, rules JSON with one rule whose description contains `DEMO_PREFIX` and whose `requests_per_period` equals the policy threshold (itself one of the two configured values); then `approve <proposal-id>` (or `--approve`; `--decline` proves the abort), a nonce per proposal, the code-owned target assertion (`edge/queries/assert_demo_rule.sql`: the rule description carries `DEMO_PREFIX`), exactly one `run_mutation_query`, verification with the same code-owned SELECT and, after `validate_select_query`, the model's verification SELECT
- decision record: appended to `runs/edge-decisions.jsonl`; when `GITHUB_DECISIONS_REPO` is set it is filed as a second gated statement. That INSERT stays code-owned (`edge/queries/decision_issue.sql`, pinned to the repo by the allowlist): no model is involved in this step and the query library has no issue-creation entry to discover
- `restore` puts `BASELINE_THRESHOLD` back with the code-owned `edge/queries/restore_rate_limit.sql` through the same allowlist and gate; no model is involved
- `edge/queries/`: `examples/` holds two illustrative SELECTs the prompts may cite as the shape, not a pack (not run by code); the rest is code-owned as listed above. `validate` renders the prompts and runs `validate_select_query` on the example and code-owned SELECTs
- end of run: per-step model, requests, input/cached/output/reasoning tokens, select vs mutation tool calls, USD from `pricing.json`, the trace URL and the audit log path
- stack `edge/stack`: one stackql-deploy resource that sets the zone's `http_ratelimit` phase to a single rule at `BASELINE_THRESHOLD` requests per 10 seconds with the `DEMO_PREFIX` description; teardown resets the phase to `[]`
- tests (`edge/tests`, offline): prompt loader (file found, placeholders substituted, missing value names the variable, briefing appended), statement allowlist (accepts the one shape; rejects another resource, another verb, multi-statement input, a SELECT, a wrong zone or phase, a foreign description, a threshold off policy), query loader over the reduced set, gate refuses without a matching approval, read-only servers never expose mutation tools and publish the instructions resource, decision record writer, policy envelope, recon cross-check

**usage:**
```
cd edge && uv sync                                   # deps incl. stackql-mcp-server
uv run python -m edge setup                          # pulls cloudflare + github, prints server_info
uv run python -m edge validate                       # renders prompts, validate_select_query on examples and code-owned SELECTs
cd .. && stackql-deploy build edge/stack dev --env-file .env    # baseline rate limit rule
cd edge && uv run python -m edge run                 # recon -> decision -> allowlist -> gate (type: approve <id>)
uv run python -m edge run --elevated-rps 0.01 --approve         # force a tighten, unattended
uv run python -m edge run --window-minutes 10 --decline        # prove the abort path
uv run python -m edge run --no-server-instructions   # local briefing only
uv run python -m edge restore --approve              # BASELINE_THRESHOLD back through the gate (code-owned statement)
uv run ruff check . && uv run pytest -q              # lint and offline tests
cd .. && stackql-deploy teardown edge/stack dev --env-file .env # empties the phase
```
Always-on trigger, every five minutes (the run holds when traffic is normal and exits without a statement):
```
*/5 * * * * cd /path/to/openai-agentic-platform-engineering/edge && uv run python -m edge run --approve >> ../runs/edge-cron.log 2>&1
```
In production the trigger is an event, not a clock: a Cloudflare notification webhook (traffic anomaly or origin error rate) or an alerting hook (PagerDuty, Grafana) calling the same `run` command, with `--approve` replaced by the terminal prompt or an approval in the ticket system.

Expected output: the instructions resource read, the recon agent's library and discovery calls and its SELECTs printed as they run, a recon table (including the resources it read), the decision agent's `list_methods` / `describe_method` calls, the decision with rationale, the proposed REPLACE at the gate marked as checked against the allowlist, `target assertion ok`, `executed via run_mutation_query`, `verified (code-owned SELECT): requests_per_period = 30`, the proposed verification rows, the decision record path, then the cost/trace table.

Environment (repo-root `.env`): `OPENAI_API_KEY`, `SWEEP_MODEL`, `REASONING_MODEL`, `SWEEP_REASONING_EFFORT`, `REASONING_REASONING_EFFORT`, `CLOUDFLARE_API_TOKEN` (Zone WAF: Edit, Analytics: Read), `CLOUDFLARE_ZONE_ID`, `DEMO_PREFIX`, `ELEVATED_RPS` (default 5), `TIGHTENED_THRESHOLD` (30), `BASELINE_THRESHOLD` (100), `WINDOW_MINUTES` (30), optional `GITHUB_DECISIONS_REPO` (owner/repo) with `STACKQL_GITHUB_USERNAME` and `STACKQL_GITHUB_PASSWORD`, `STACKQL_APPROOT`, `STACKQL_MCP_AUDIT_LOG`, `EDGE_DECISIONS_LOG`, `OPENAI_PRICING_JSON`.
