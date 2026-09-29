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
