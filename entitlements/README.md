## Entitlements audit

**description:**
A scheduled, read-only recertification sweep over five control planes: AWS, Azure, Google Cloud, GitHub and the identity provider (Microsoft Entra ID, or Okta with `IDP_PROVIDER=okta`). One StackQL MCP server process holds the federated credentials for all of them, and the agent audits them in one result set with SQL joins: privileged principals across the three clouds in a single UNION, GitHub org members LEFT JOIN IdP users (orphans and leavers), the privileged IdP group joined to user objects (disabled accounts still holding privilege), and outside collaborators with admin on demo repos. It produces a console table and brief, a run record `runs/entitlements-<ts>.json`, and a recertification report `runs/entitlements-recertification-<ts>.md` with a confirm/revoke column, evidence and the removal statement per finding.

**intent:**
Always-on (cron-triggered, nobody types a prompt), read-heavy (SELECTs against control-plane metadata only, no customer data) and non-mutating: the report drafts the StackQL statement that would remove each grant, and this program never executes it. The mini-tier model does the sweep and classification; only findings at or above `ESCALATION_SEVERITY` reach the frontier model, so cost scales with the estate and the finding count rather than seats. The model-facing server runs in `read_only` mode with the mutation and admin tools filtered out, which is what survives an enterprise security review where customer-facing agents stall.

**implementation:**
- TypeScript on Node 22, `@openai/agents` 0.18 with zod 4 schemas as the structured outputs (findings, assessments) and `withTrace` for the trace id; run with `tsx`, typechecked with `tsc`
- StackQL MCP server from npm (`@stackql/mcp-server`, v0.12.718): the launcher in `node_modules` is spawned by `MCPServerStdio` with the full process environment, `--mcp.config` `{"server":{"mode":"read_only","audit":{"file":...}}}` and `createMCPToolStaticFilter` allowing only the discovery, `run_select_query`, `validate_select_query` and query-library tools; `run_mutation_query`, `run_lifecycle_operation`, `pull_provider` and `reload_credentials` are blocked (`entitlements/src/mcp.ts`)
- Two model tiers from the environment: `SWEEP_MODEL` (classification, `SWEEP_REASONING_EFFORT`) and `REASONING_MODEL` (who should confirm, least-privilege alternative, removal statement; `REASONING_REASONING_EFFORT`); no model id in code
- Queries live in `entitlements/queries/*.sql` (header: id, providers, params, expected_columns, description, optional idp); the `render_query` function tool renders them with fan-out parameters (JSON arrays -> IN lists or UNION ALL); `entra_id` and `okta` variants of the IdP queries are selected by `IDP_PROVIDER`
- Stack: `entitlements/stack/aws` (IAM user `<DEMO_PREFIX>-admin` with AdministratorAccess) and `entitlements/stack/entra_id` (group `<DEMO_PREFIX>-cloud-admins` with a disabled member `<DEMO_PREFIX>-leaver`); no Azure or GCP writes - see `entitlements/stack/README.md`
- Every run ends with the cost and trace block: per tier the requests, input/cached/output/reasoning tokens, tool calls (select vs mutation), USD from `pricing.json` (`OPENAI_PRICING_JSON` overrides), the trace URL and the audit log path

**usage:**
```
cd entitlements && npm install
npm run setup                     # pull_provider for aws, azure, google, github and the IdP; prints server_info
npm run validate                  # validate_select_query over every query (CTE bodies per provider, sqlite shape check)
stackql-deploy build entitlements/stack/aws dev --env-file .env        # from the repo root
stackql-deploy build entitlements/stack/entra_id dev --env-file .env
npm run sweep                     # one run: table + brief, runs/entitlements-<ts>.json, the recertification report, cost/trace
npm run sweep -- --dry-run        # print the query pack and the 11 tools the model sees, validate the static SELECTs, call no model
npm run sweep -- --idp okta       # same sweep against Okta instead of Entra ID
npm test                          # query loader, render helpers, findings schema, read-only server invariant
stackql-deploy teardown entitlements/stack/entra_id dev --env-file .env
stackql-deploy teardown entitlements/stack/aws dev --env-file .env
```
Schedule it with cron (weekly recertification, Monday 06:10 UTC):
```
10 6 * * 1  cd /opt/openai-agentic-platform-engineering/entitlements && npm run sweep >> ../runs/entitlements-cron.log 2>&1
```
or with a GitHub Actions cron workflow that checks out the repo, runs `npm ci && npm run sweep` with the `.env` values as secrets, and uploads `runs/` as an artifact. Expected output: a findings table (critical: leaver still privileged; high: non-human principal with owner/admin, outside collaborator admin; medium: orphan member, human Owner at subscription scope), the reasoning tier's assessment of the escalated ones, the report path, and the cost/trace block.
