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
