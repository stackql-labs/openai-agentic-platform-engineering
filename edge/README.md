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
