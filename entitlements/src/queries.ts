/**
 * Query library loader. All SQL lives in entitlements/queries/ - one file per query with a
 * header comment:
 *
 *   -- id: entitlements/aws_iam_users
 *   -- providers: aws
 *   -- params: aws_iam_region
 *   -- expected_columns: user_name, user_id, arn, ...
 *   -- description: one line
 *   -- idp: entra_id            (optional: the query belongs to one IdP variant)
 *
 * `{{ param }}` placeholders are substituted from environment variables (lower-cased param ->
 * the matching upper-case variable), then derived defaults (config.derivedParams), then explicit
 * overrides passed by code. Missing values fail fast naming the variable. Agent code loads
 * queries by id; there are no inline SQL strings in agent code.
 */

import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { QUERIES_DIR, derivedParams, env } from './config.ts';

const HEADER_RE = /^--\s*([a-z_]+)\s*:\s*(.*)$/;
const PARAM_RE = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;

export interface Query {
  id: string;
  path: string;
  providers: string[];
  params: string[];
  expectedColumns: string[];
  description: string;
  idp: string | null;
  sql: string;
  meta: Record<string, string>;
}

function splitList(v: string | undefined): string[] {
  return (v ?? '')
    .split(',')
    .map((x) => x.trim())
    .filter((x) => x !== '');
}

export function parseQuery(text: string, filePath: string, dir: string = QUERIES_DIR): Query {
  const meta: Record<string, string> = {};
  const body: string[] = [];
  let inHeader = true;
  for (const line of text.split(/\r?\n/)) {
    if (inHeader) {
      const m = HEADER_RE.exec(line.trim());
      if (m) {
        meta[m[1]!] = m[2]!.trim();
        continue;
      }
      if (line.trim() === '' || line.trim().startsWith('--')) continue;
      inHeader = false;
    }
    body.push(line);
  }
  const sql = body.join('\n').trim().replace(/;\s*$/, '');
  const rel = path.relative(dir, filePath).replace(/\\/g, '/').replace(/\.sql$/, '');
  const declared = splitList(meta.params);
  const used = [...new Set([...sql.matchAll(PARAM_RE)].map((m) => m[1]!))].sort();
  return {
    id: meta.id ?? `entitlements/${rel}`,
    path: filePath,
    providers: splitList(meta.providers),
    params: declared.length ? declared : used,
    expectedColumns: splitList(meta.expected_columns),
    description: meta.description ?? '',
    idp: meta.idp ?? null,
    sql,
    meta,
  };
}

export function placeholdersIn(sql: string): string[] {
  return [...new Set([...sql.matchAll(PARAM_RE)].map((m) => m[1]!))];
}

let cache: Query[] | null = null;

export function listQueries(dir: string = QUERIES_DIR, useCache = true): Query[] {
  if (useCache && dir === QUERIES_DIR && cache) return cache;
  const files = readdirSync(dir)
    .filter((f) => f.endsWith('.sql'))
    .sort();
  const out = files.map((f) => {
    const p = path.join(dir, f);
    return parseQuery(readFileSync(p, 'utf8'), p, dir);
  });
  const seen = new Set<string>();
  for (const q of out) {
    if (seen.has(q.id)) throw new Error(`duplicate query id ${q.id} under ${dir}`);
    seen.add(q.id);
  }
  if (dir === QUERIES_DIR) cache = out;
  return out;
}

export function loadQuery(id: string): Query {
  const q = listQueries().find((x) => x.id === id);
  if (!q) throw new Error(`no query with id '${id}' under ${QUERIES_DIR}`);
  return q;
}

/** Parameter values: env var (upper-cased param) -> derived default -> explicit override. */
export function paramValues(overrides: Record<string, string> = {}): Record<string, string> {
  const derived = derivedParams();
  const out: Record<string, string> = { ...derived };
  for (const k of Object.keys(derived)) {
    const v = env(k.toUpperCase());
    if (v !== '') out[k] = v;
  }
  for (const [k, v] of Object.entries(overrides)) out[k] = String(v);
  return out;
}

export function renderQuery(q: Query, overrides: Record<string, string> = {}): string {
  const values = paramValues(overrides);
  const resolve = (key: string): string => {
    if (key in values && values[key] !== '') return values[key]!;
    const fromEnv = env(key.toUpperCase());
    if (fromEnv !== '') return fromEnv;
    throw new Error(
      `query ${q.id}: parameter '${key}' has no value - set ${key.toUpperCase()} in .env or pass it explicitly`,
    );
  };
  const missing = q.params.filter((p) => {
    try {
      resolve(p);
      return false;
    } catch {
      return true;
    }
  });
  if (missing.length) {
    throw new Error(
      `query ${q.id}: missing parameters ${missing.join(', ')} (set ${missing
        .map((m) => m.toUpperCase())
        .join(', ')} in .env or pass them explicitly)`,
    );
  }
  return q.sql.replace(PARAM_RE, (_m, key: string) => resolve(key)).trim();
}

/** Render a literal IN (...) list: fan-out parameters must be literals (no subqueries). */
export function sqlList(values: string[]): string {
  return values.map((v) => `'${v.replace(/'/g, "''")}'`).join(', ');
}

/**
 * Render one SELECT per value and UNION ALL them. Used where a provider method takes one key
 * per call and the response does not echo it (attached policies per user): each SELECT projects
 * its key as a literal column so rows stay attributed.
 */
export function renderUnion(
  q: Query,
  param: string,
  values: string[],
  overrides: Record<string, string> = {},
): string {
  if (!values.length) throw new Error(`renderUnion(${q.id}): no values for ${param}`);
  return values.map((v) => renderQuery(q, { ...overrides, [param]: v })).join('\nUNION ALL\n');
}
