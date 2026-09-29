## Edge autopilot

**description:**
Rebuilds the stackql/edgepilot pattern on OpenAI: a recon agent reads a Cloudflare zone's live HTTP analytics and its rate limit ruleset over the last N minutes, a decision step judges whether traffic is elevated against a threshold policy, and when it is, the rate limit is tightened by a single REPLACE statement that runs only after explicit approval. Providers: cloudflare (recon and the gated change) and github (optional decision record as an issue). It produces a recon report, a structured decision, an audited SQL statement, a decision record (`runs/edge-decisions.jsonl` or a GitHub issue) and a run file under `runs/`.

**intent:**
An always-on control loop that is not a chat: it fires from a schedule or an alert hook, spends almost all of its tokens on SELECTs against a control plane (traffic counts and rule config, never request bodies or customer data), and puts the one write behind a human phrase, a target assertion and a post-change verification. The cheap tier reads, the frontier tier reasons, and code, not the model, renders and executes the statement, so the whole run is reviewable as SQL in the audit log. The original published its decision to Confluent Kafka; StackQL has a confluent provider, so the same record could go to a topic, but this demo does not build that sink.

**implementation:**
- Python 3.12, uv, `openai-agents` (Agents SDK), `pydantic` structured outputs, `rich` console
- StackQL MCP server from PyPI (`stackql-mcp-server`): the `stackql-mcp` console script in `edge/.venv` is spawned by `MCPServerStdio`; every statement is echoed to the console as it runs
- recon agent: `SWEEP_MODEL`, server in `read_only` mode with the mutation and admin tools filtered out; runs `edge/queries/zone_traffic.sql` and `edge/queries/rate_limit_ruleset.sql` verbatim and returns a `ReconReport` (total requests, distinct countries, non-2xx share, requests per second, ruleset id, rule id, threshold, period); code recomputes the arithmetic from the returned rows
- decision: `REASONING_MODEL`, no tools; returns `Decision {action: tighten|hold, new_threshold, rationale, risk}`; the policy (`ELEVATED_RPS`, `TIGHTENED_THRESHOLD`, `BASELINE_THRESHOLD`) is enforced again in code, so only the two configured thresholds can reach the gate; the rollback statement is rendered from the query file, not authored by the model
- gate and executor (`edge/edge/gate.py`, the only code that starts a `full_access` server, never given to a model): prints the rendered `edge/queries/tighten_rate_limit.sql`, requires `approve <proposal-id>` (or `--approve`; `--decline` proves the abort), asserts with a SELECT on the same server that the rule description contains `DEMO_PREFIX`, runs exactly one `run_mutation_query`, then re-reads the ruleset and checks the threshold
- decision record: appended to `runs/edge-decisions.jsonl`; when `GITHUB_DECISIONS_REPO` is set it is filed with `INSERT INTO github.issues.issues` (`edge/queries/decision_issue.sql`) as a second gated statement after a repo assertion
- end of run: per-step model, requests, input/cached/output/reasoning tokens, select vs mutation tool calls, USD from `pricing.json`, the trace URL and the audit log path
- stack `edge/stack`: one stackql-deploy resource that sets the zone's `http_ratelimit` phase to a single rule at `BASELINE_THRESHOLD` requests per 10 seconds with the `DEMO_PREFIX` description; teardown resets the phase to `[]`
- tests (`edge/tests`, offline): query loader, gate refuses without a matching approval, read-only servers never expose mutation tools, decision record writer, policy envelope, recon cross-check

**usage:**
```
cd edge && uv sync                                   # deps incl. stackql-mcp-server
uv run python -m edge setup                          # pulls cloudflare + github, prints server_info
uv run python -m edge validate                       # validate_select_query on every SELECT
cd .. && stackql-deploy build edge/stack dev --env-file .env    # baseline rate limit rule
cd edge && uv run python -m edge run                 # recon -> decision -> gate (type: approve <id>)
uv run python -m edge run --elevated-rps 0.01 --approve         # force a tighten, unattended
uv run python -m edge run --window-minutes 10 --decline        # prove the abort path
uv run python -m edge restore --approve              # BASELINE_THRESHOLD back through the gate
uv run ruff check . && uv run pytest -q              # lint and offline tests
cd .. && stackql-deploy teardown edge/stack dev --env-file .env # empties the phase
```
Always-on trigger, every five minutes (the run holds when traffic is normal and exits without a statement):
```
*/5 * * * * cd /path/to/openai-agentic-platform-engineering/edge && uv run python -m edge run --approve >> ../runs/edge-cron.log 2>&1
```
In production the trigger is an event, not a clock: a Cloudflare notification webhook (traffic anomaly or origin error rate) or an alerting hook (PagerDuty, Grafana) calling the same `run` command, with `--approve` replaced by the terminal prompt or an approval in the ticket system.

Expected output: the two SELECTs printed as they run, a recon table, the decision with rationale, the rendered REPLACE at the gate, `target assertion ok`, `executed via run_mutation_query`, `verified: requests_per_period = 30`, the decision record path, then the cost/trace table.

Environment (repo-root `.env`): `OPENAI_API_KEY`, `SWEEP_MODEL`, `REASONING_MODEL`, `SWEEP_REASONING_EFFORT`, `REASONING_REASONING_EFFORT`, `CLOUDFLARE_API_TOKEN` (Zone WAF: Edit, Analytics: Read), `CLOUDFLARE_ZONE_ID`, `DEMO_PREFIX`, `ELEVATED_RPS` (default 5), `TIGHTENED_THRESHOLD` (30), `BASELINE_THRESHOLD` (100), `WINDOW_MINUTES` (30), optional `GITHUB_DECISIONS_REPO` (owner/repo) with `STACKQL_GITHUB_USERNAME` and `STACKQL_GITHUB_PASSWORD`, `STACKQL_APPROOT`, `STACKQL_MCP_AUDIT_LOG`, `EDGE_DECISIONS_LOG`, `OPENAI_PRICING_JSON`.
