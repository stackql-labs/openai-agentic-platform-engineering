## FinOps / cost optimisation audit

**description:**
A scheduled, read-only sweep that finds idle and orphaned resources across AWS, Azure and Google Cloud: unattached EBS volumes, managed disks and persistent disks, unassociated Elastic IPs and public IPs, and snapshots older than a threshold. Each row carries a monthly cost estimate computed in SQL from list-price constants (labelled as estimates, not billing data). A mini-tier model classifies the rows into the shared findings shape; a reasoning-tier model then decides, per provider, what is safe to delete now and drafts a batch of StackQL DELETE/EXEC statements ordered by savings, for human review only. It produces a console table and brief, `runs/finops-<ts>.json`, a markdown cost report `runs/finops-<ts>.md`, and a cost/trace block.

**intent:**
The workload runs on a timer and costs tokens in proportion to estate size, not seats: nobody types a prompt, and every step is a SELECT against a cloud control plane (metadata only). The StackQL MCP server is started in `read_only` mode and the mutation tools are filtered out of both models' tool lists, so even a prompt-injected DELETE cannot execute; the statements the reasoning tier drafts are reviewable, replayable SQL that an engineer runs, or not. Two model tiers keep the recurring cost on the mini model and spend frontier-model tokens only on the judgement step, which is the profile that gets through an enterprise security review.

**implementation:**
- Python 3.12, uv, `openai-agents` (Agents SDK: `Agent`, `Runner`, `MCPServerStdio`, structured outputs, tracing).
- StackQL MCP server from PyPI `stackql-mcp-server`, a dependency of `finops/pyproject.toml`; its `stackql-mcp` console script is launched from the project venv by `MCPServerStdio` as a stdio subprocess (the signed `stackql` binary is downloaded on first run). Server mode `read_only` with an audit file (`STACKQL_MCP_AUDIT_LOG`); a static tool filter hides `run_mutation_query`, `run_lifecycle_operation`, `pull_provider` and `reload_credentials` from the models. There is no mutation path in this use case.
- Model tiers from `.env`: `SWEEP_MODEL` runs the query pack and classifies (output type `FindingSet`); `REASONING_MODEL` receives all findings batched per provider and returns a `ProviderPlan` (proposed statements with reasons, deferred items with reasons). Model ids are never in code.
- Queries: `finops/queries/*.sql`, one SELECT per file with a header (id, providers, params, expected_columns, description); params come from env (`AWS_REGION`, `AZURE_SUBSCRIPTION_ID`, `GOOGLE_PROJECT`, `SNAPSHOT_MAX_AGE_DAYS`). `finops/queries/remediation/*.sql` hold the DELETE/EXEC templates the models fill in; nothing in code executes them.
- Stacks: `finops/stack/aws` (1 GB gp3 volume attached to nothing, unassociated Elastic IP), `finops/stack/azure` (resource group, 4 GB Standard_LRS disk attached to nothing, Standard static public IP associated with nothing), `finops/stack/google` (10 GB pd-standard disk attached to nothing). One stack per provider so one cloud is enough; everything is named with `DEMO_PREFIX` and tagged/labelled `DEMO_TAG_KEY=DEMO_TAG_VALUE`.
- Cost block from the repo-root `pricing.json` (`OPENAI_PRICING_JSON` extends it); trace URL from the Agents SDK trace id.

**usage:**
```
cd finops && uv sync                                        # deps incl. the MCP server package
uv run python -m finops setup                               # pull aws, azure, google; print server_info
uv run python -m finops validate                            # validate_select_query on every query
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

Expected output: a findings table (severity, provider, finding, resource, estimated USD/month) and a brief; per provider, the proposed statements with reasons and the deferred items; the paths of `runs/finops-<ts>.md` and `runs/finops-<ts>.json`; then the cost/trace table (per step model, requests, input/cached/output/reasoning tokens, select vs mutation tool calls - always 0 mutation - USD), the trace URL and the audit log path.
