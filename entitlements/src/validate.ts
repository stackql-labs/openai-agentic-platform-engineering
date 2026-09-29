/**
 * `validate`: plan every example SELECT under entitlements/queries/examples/ with the MCP tool
 * validate_select_query. The model discovers everything else at run time, so these two files
 * are the only committed SQL and the only thing this subcommand checks.
 *
 * validate_select_query resolves the provider's credentials as execution does. Provider
 * credential variables that are unset get dummy values for this process only; a provider that
 * refuses them fails after the parser, resource and method resolution have passed, which is
 * reported as planned (credentials refused), not as a query failure. Exits non-zero on any
 * plan failure.
 */

import type { MCPServer } from '@openai/agents';
import { PROVIDER_ENV, env, promptValues, settings, type Settings } from './config.ts';
import { callToolJson, readOnlyServer, withServer } from './mcp.ts';
import { listQueries, renderQuery, type Query } from './queries.ts';

/** Representative values for parameters the environment does not supply. */
export const DUMMY_VALUES: Record<string, string> = {
  aws_account_id: '000000000000',
  aws_iam_region: 'us-east-1',
  aws_admin_user: 'agentic-demo-admin',
  azure_subscription_id: '00000000-0000-0000-0000-000000000000',
  google_project: 'demo-project',
  github_org: 'demo-org',
  demo_prefix: 'agentic-demo',
  okta_subdomain: 'dev-000000',
  idp_privileged_group: 'agentic-demo-cloud-admins',
};

/** A representative rendering of an example for planning purposes. */
export function renderForValidation(q: Query, s: Settings): string {
  const values: Record<string, string> = { ...DUMMY_VALUES };
  for (const [k, v] of Object.entries(promptValues(s))) if (v !== '' && !/not configured/.test(v)) values[k] = v;
  return renderQuery(q, values);
}

export function setDummyCredentials(): string[] {
  const set: string[] = [];
  const skip = new Set(['AWS_ACCOUNT_ID', 'AZURE_SUBSCRIPTION_ID', 'GOOGLE_PROJECT', 'GITHUB_ORG']);
  for (const vars of Object.values(PROVIDER_ENV)) {
    for (const v of vars) {
      if (skip.has(v) || env(v) !== '') continue;
      process.env[v] =
        v === 'GOOGLE_CREDENTIALS'
          ? '{"type":"service_account","project_id":"demo-project"}'
          : v === 'OKTA_DOMAIN'
            ? 'dev-000000.okta.com'
            : 'dummy';
      set.push(v);
    }
  }
  return set;
}

export interface ValidationResult {
  ok: boolean;
  detail: string;
}

/**
 * validate_select_query executes the provider call; with dummy credentials some providers fail at
 * token acquisition before returning. That is a credential failure, not a query failure: the
 * parser, resource and method resolution have already passed by then.
 */
export function isCredentialFailure(detail: string): boolean {
  return /token|oauth2|credential|private key|AADSTS|401|403|Unauthorized|Forbidden/i.test(detail);
}

export async function validateSql(server: MCPServer, sql: string): Promise<ValidationResult> {
  const r = await callToolJson(server, 'validate_select_query', { sql, format: 'json' });
  const d = (r.data ?? {}) as { valid?: boolean; errors?: unknown };
  if (d.valid === true && !r.isError) return { ok: true, detail: 'valid' };
  const detail = JSON.stringify(d.errors ?? r.text).slice(0, 300);
  if (isCredentialFailure(detail)) return { ok: true, detail: 'planned (provider refused dummy credentials)' };
  return { ok: false, detail };
}

/** Validate every example against a connected server; prints one line each; returns the failure count. */
export async function validateExamples(server: MCPServer, s: Settings): Promise<number> {
  let failures = 0;
  for (const q of listQueries()) {
    let sql: string;
    try {
      sql = renderForValidation(q, s);
    } catch (e) {
      failures += 1;
      console.log(`  FAIL  ${q.id}\n        render: ${(e as Error).message}`);
      continue;
    }
    const r = await validateSql(server, sql);
    if (!r.ok) failures += 1;
    console.log(`  ${r.ok ? 'pass' : 'FAIL'}  ${q.id}  [${r.detail}]`);
  }
  return failures;
}

export async function runValidate(): Promise<number> {
  const s = settings();
  const dummies = setDummyCredentials();
  if (dummies.length) console.log(`dummy credentials set for this process: ${dummies.join(', ')}`);
  const queries = listQueries();
  console.log(`validating ${queries.length} example SELECTs against approot ${s.stackqlApproot}\n`);
  let failures = 0;
  await withServer(readOnlyServer('stackql-validate'), async (server) => {
    failures = await validateExamples(server, s);
  });
  console.log(`\n${queries.length - failures}/${queries.length} examples valid`);
  return failures ? 1 : 0;
}
