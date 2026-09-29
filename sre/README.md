## Agentic SRE - gated remediation on Kubernetes

**description:**
A synthetic alert (checkout p95 over SLO, request queue growing on the only ready replica) fires against a kind cluster; nobody types a prompt. The agent diagnoses with SELECTs over the k8s provider (deployment spec vs ready replicas, pod phase and restarts, Warning events), proposes one action from a fixed menu (scale out by one, or no action), and stops at an approval gate. The operator approves the rendered `UPDATE k8s.apps.deployments_scale ...` statement in the terminal; code asserts the target is the demo deployment, executes that one statement, polls the verification SELECT until ready replicas match, and a mini-tier model writes the close-out note. Output: the terminal transcript, `runs/sre-<timestamp>-<id>.json`, and a statement log.

**intent:**
Closed-loop incident response where the model reasons and code acts: always-on (event trigger, not a chat), read-heavy (every diagnostic step is a SELECT against the Kubernetes control plane, no workload data), and the single mutation is a reviewable SQL statement that runs only after a human types the approval phrase and only against a target that carries the demo namespace, name prefix and label. Two model tiers keep the frontier model on diagnosis and planning and the mini tier on the write-up; the run ends with tokens, USD and response ids on screen. The model never holds a mutation tool, which is what an enterprise security review asks for first.

**implementation:**
- Rust (edition 2021), `async-openai` 0.42 on the Responses API: a function-calling loop whose tools are bridged from the MCP server's tool list, and structured outputs via `json_schema` text formats (`diagnosis`, `proposal`, `closeout`).
- crates.io `stackql-mcp` 0.12: the StackQL MCP server is embedded, started in-process over stdio. Default `sidecar` feature downloads the sha256-pinned binary into `~/.stackql/mcp-server-bin/` on first run (set `STACKQL_MCP_BIN` to reuse one); the crate's `vendored` feature embeds the `.mcpb` with `include_bundle!()` for a single shippable binary with no runtime download.
- Server modes: every model-facing server runs `read_only`, and `sre/src/mcp.rs` filters the tool list to `run_select_query`, `validate_select_query` and the `list_*` / `describe_*` discovery tools. `sre/src/gate.rs` is the only module that starts a `full_access` server and the only caller of `run_mutation_query`; a unit test scans the source tree to keep it that way.
- Gate: `prepare` renders `sre/queries/scale_deployment.sql` and mints a nonce; an approval exists only from the exact phrase `approve <proposal-id>` (or `--approve`); `execute_approved` re-checks the nonce, runs `sre/queries/assert_demo_target.sql` on the executor server (namespace = `K8S_NAMESPACE`, name starts with `DEMO_PREFIX` or equals `SRE_TARGET_DEPLOYMENT`, label `DEMO_TAG_KEY=DEMO_TAG_VALUE`), then sends exactly one statement. `--decline` proves the abort path with zero mutations.
- Queries: `sre/queries/*.sql`, one per query with a parsed header (`id`, `providers`, `params`, `expected_columns`, `description`); `{{ param }}` placeholders resolve from `.env` (`k8s_namespace` -> `K8S_NAMESPACE`) plus code overrides. Every statement carries `cluster_addr` and `protocol`. The scale mutation uses the `deployments_scale` patch method (`application/merge-patch+json`): `UPDATE k8s.apps.deployments_scale SET spec = '{"replicas": N}' WHERE name = ... AND namespace = ... AND cluster_addr = ... AND protocol = ...`.
- Model tiers from `.env`: `REASONING_MODEL` diagnoses and proposes, `SWEEP_MODEL` writes the close-out. Cost from the repo-root `pricing.json` (`OPENAI_PRICING_JSON` overrides).
- The embedded crate launches the server with its own audit disabled, so the program writes its own statement log: one JSON line per tool call (mode, tool, SQL, result) at `STACKQL_MCP_AUDIT_LOG`.
- Stack: `sre/stack` (stackql-deploy, provider k8s) creates the namespace, a one-replica `<DEMO_PREFIX>-checkout` deployment (`hashicorp/http-echo`, 10m CPU / 16Mi requests) and a ClusterIP service, all labelled `purpose=agentic-demo`.

**usage:**
```
kind create cluster --name agentic-demo          # local cluster; no cloud credentials involved
kubectl proxy --port=8001 &                      # KUBE_CLUSTER_ADDR=localhost:8001, KUBE_PROTOCOL=http
cd sre && cargo build --release && ./target/release/sre setup    # pulls the k8s provider, prints server_info
cd .. && stackql-deploy build sre/stack dev --env-file .env      # namespace + deployment (1 replica) + service
cd sre && ./target/release/sre validate-queries  # validate_select_query over every SELECT in sre/queries/

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
Expected output: the alert, the diagnosis with the replica and pod numbers, the proposal, the rendered UPDATE at the gate, the target assertion, one `run_mutation_query`, the verify poll reaching `ready=2`, the close-out paragraph, then the cost/trace block (per step tokens, USD, response ids, statement log and run record paths). Variables read (all from the repo-root `.env`): `OPENAI_API_KEY`, `SWEEP_MODEL`, `REASONING_MODEL`, `SWEEP_REASONING_EFFORT`, `REASONING_REASONING_EFFORT`, `OPENAI_PRICING_JSON`, `DEMO_PREFIX`, `DEMO_TAG_KEY`, `DEMO_TAG_VALUE`, `STACKQL_APPROOT`, `STACKQL_MCP_AUDIT_LOG`, `STACKQL_MCP_BIN`, `KUBE_CLUSTER_ADDR`, `KUBE_PROTOCOL`, `K8S_NAMESPACE`, and optionally `SRE_TARGET_DEPLOYMENT` (default `<DEMO_PREFIX>-checkout`), `SRE_APP_LABEL` (`checkout`), `SRE_VERIFY_TIMEOUT_SECS` (120), `SRE_VERIFY_POLL_SECS` (5).
