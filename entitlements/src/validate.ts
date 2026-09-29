/**
 * `validate`: check every committed SELECT under entitlements/queries/.
 *
 * validate_select_query (stackql 0.12.718) accepts plain SELECTs but rejects `WITH ... AS (`
 * statements that run_select_query executes without complaint. So:
 *   - a plain SELECT goes to validate_select_query as is;
 *   - a CTE query has each CTE body (the provider-facing SELECT) validated through the tool, and
 *     the whole statement shape-checked offline with node:sqlite (StackQL's backend engine) by
 *     replacing each CTE body with a stub table carrying the body's projected columns.
 * Fan-out parameters are rendered with placeholder values; provider credential variables that
 * are unset get dummy values for this process only. Prints pass/fail per query and exits
 * non-zero on any failure.
 */

import type { MCPServer } from '@openai/agents';
import { PROVIDER_ENV, env, settings } from './config.ts';
import { callToolJson, readOnlyServer, withServer } from './mcp.ts';
import { listQueries, renderQuery, renderUnion, sqlList, type Query } from './queries.ts';
import { projectedColumns, resourceOf, sourceColumns, splitCtes, unionColumns } from './sqlparts.ts';
import { AWS_ADMIN_LITERAL_QUERY, LIST_PARAMS, UNION_PARAMS } from './tools.ts';

const DUMMY_VALUES: Record<string, string> = {
  aws_account_id: '000000000000',
  azure_subscription_id: '00000000-0000-0000-0000-000000000000',
  google_project: 'demo-project',
  github_org: 'demo-org',
  okta_subdomain: 'dev-000000',
  idp_group_id: '00000000-0000-0000-0000-000000000001',
  idp_privileged_group: 'agentic-demo-cloud-admins',
};

/** A representative rendering of a query for planning purposes. */
export function renderForValidation(q: Query, all: Map<string, Query>): string {
  const overrides: Record<string, string> = {};
  let union: { param: string; values: string[] } | null = null;
  for (const p of q.params) {
    if (p === 'aws_admins_sql') {
      const lit = all.get(AWS_ADMIN_LITERAL_QUERY);
      if (!lit) throw new Error(`${AWS_ADMIN_LITERAL_QUERY} missing`);
      overrides[p] = renderUnion(lit, 'user_name', ['alice', 'bob']);
    } else if (LIST_PARAMS.has(p)) {
      overrides[p] = sqlList(['agentic-demo-a', 'agentic-demo-b']);
    } else if (UNION_PARAMS.has(p)) {
      union = { param: p, values: ['alice', 'bob'] };
    } else if (env(p.toUpperCase()) === '' && p in DUMMY_VALUES) {
      overrides[p] = DUMMY_VALUES[p]!;
    }
  }
  return union ? renderUnion(q, union.param, union.values, overrides) : renderQuery(q, overrides);
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

export interface PartResult {
  label: string;
  ok: boolean;
  detail: string;
}

/**
 * validate_select_query executes the provider call; with dummy credentials some providers fail at
 * token acquisition before the column check runs. That is a credential failure, not a query
 * failure: the parser, resource and method resolution have already passed by then.
 */
export function isCredentialFailure(detail: string): boolean {
  return /token|oauth2|credential|private key|AADSTS|401|403|Unauthorized|Forbidden/i.test(detail);
}

async function describedColumns(server: MCPServer, sql: string): Promise<{ resource: string; columns: string[] } | null> {
  const res = resourceOf(sql);
  if (!res) return null;
  const r = await callToolJson(server, 'describe_resource', { ...res, format: 'json' });
  const rows = ((r.data ?? {}) as { rows?: { name: string }[] }).rows ?? [];
  return { resource: `${res.provider}.${res.service}.${res.resource}`, columns: rows.map((x) => x.name) };
}

async function validateOne(server: MCPServer, sql: string): Promise<PartResult> {
  const r = await callToolJson(server, 'validate_select_query', { sql, format: 'json' });
  const d = (r.data ?? {}) as { valid?: boolean; errors?: unknown };
  if (d.valid === true && !r.isError) return { label: 'validate_select_query', ok: true, detail: '' };
  const detail = JSON.stringify(d.errors ?? r.text).slice(0, 300);
  if (!isCredentialFailure(detail)) return { label: 'validate_select_query', ok: false, detail };
  // planned (parser, resource and method resolved); check the columns offline instead
  const described = await describedColumns(server, sql);
  if (!described) return { label: 'validate_select_query', ok: false, detail };
  const missing = sourceColumns(sql).filter((c) => !described.columns.includes(c));
  if (missing.length) {
    return {
      label: 'validate_select_query',
      ok: false,
      detail: `planned, but columns ${missing.join(', ')} are not in describe_resource ${described.resource}`,
    };
  }
  return {
    label: 'validate_select_query (planned; provider refused dummy credentials; columns checked with describe_resource)',
    ok: true,
    detail: '',
  };
}

/** Offline shape check of a CTE statement with node:sqlite stub tables. */
export async function sqliteShapeCheck(sql: string): Promise<PartResult> {
  const { ctes, outer } = splitCtes(sql);
  // node:sqlite is stable enough for a parse check; keep its experimental warning out of the output
  const listeners = process.listeners('warning');
  process.removeAllListeners('warning');
  process.on('warning', (w) => {
    if (w.name !== 'ExperimentalWarning') console.warn(w);
  });
  try {
    const { DatabaseSync } = await import('node:sqlite');
    const db = new DatabaseSync(':memory:');
    const stubs: string[] = [];
    for (const c of ctes) {
      const isLiteralUnion = !/\bFROM\b/i.test(c.body);
      const cols = isLiteralUnion ? unionColumns(c.body) : projectedColumns(c.body);
      db.exec(`CREATE TABLE stub_${c.name} (${cols.map((x) => `"${x}"`).join(', ')})`);
      stubs.push(`${c.name} AS (SELECT * FROM stub_${c.name})`);
    }
    const rewritten = `WITH ${stubs.join(', ')} ${outer}`;
    db.prepare(rewritten).all();
    db.close();
    return { label: 'sqlite shape check', ok: true, detail: '' };
  } catch (e) {
    return { label: 'sqlite shape check', ok: false, detail: (e as Error).message.slice(0, 300) };
  } finally {
    process.removeAllListeners('warning');
    for (const l of listeners) process.on('warning', l as (w: Error) => void);
  }
}

/** Validate one rendered statement: plain SELECT via the tool, CTE query part by part. */
export async function validateSql(server: MCPServer, sql: string): Promise<PartResult[]> {
  const { ctes } = splitCtes(sql);
  if (!ctes.length) return [await validateOne(server, sql)];
  const parts: PartResult[] = [];
  for (const c of ctes) {
    if (!/\bFROM\b/i.test(c.body)) {
      parts.push({ label: `cte ${c.name} (literal rows, no provider)`, ok: true, detail: '' });
      continue;
    }
    const r = await validateOne(server, c.body);
    const note = r.label.includes('planned') ? ' (planned; dummy credentials refused; columns checked with describe_resource)' : '';
    parts.push({ ...r, label: `cte ${c.name}${note}` });
  }
  parts.push(await sqliteShapeCheck(sql));
  return parts;
}

export async function runValidate(): Promise<number> {
  const s = settings();
  const dummies = setDummyCredentials();
  if (dummies.length) console.log(`dummy credentials set for this process: ${dummies.join(', ')}`);
  const queries = listQueries();
  const all = new Map(queries.map((q) => [q.id, q]));
  console.log(`validating ${queries.length} queries against approot ${s.stackqlApproot}\n`);
  const results: { id: string; ok: boolean; parts: PartResult[] }[] = [];
  await withServer(readOnlyServer('stackql-validate'), async (server) => {
    for (const q of queries) {
      let sql: string;
      try {
        sql = renderForValidation(q, all);
      } catch (e) {
        results.push({ id: q.id, ok: false, parts: [{ label: 'render', ok: false, detail: (e as Error).message }] });
        continue;
      }
      const parts = await validateSql(server, sql);
      results.push({ id: q.id, ok: parts.every((p) => p.ok), parts });
    }
  });
  for (const r of results) {
    console.log(`${r.ok ? 'pass' : 'FAIL'}  ${r.id}  [${r.parts.map((p) => `${p.label}: ${p.ok ? 'ok' : 'FAIL'}`).join('; ')}]`);
    for (const p of r.parts) if (!p.ok) console.log(`      ${p.label}: ${p.detail}`);
  }
  const failed = results.filter((r) => !r.ok).length;
  console.log(`\n${results.length - failed}/${results.length} queries valid`);
  return failed ? 1 : 0;
}
