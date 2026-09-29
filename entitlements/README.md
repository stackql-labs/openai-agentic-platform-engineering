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
