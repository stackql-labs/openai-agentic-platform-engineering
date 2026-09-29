/**
 * Loader for the example SELECTs under entitlements/queries/examples/. These are not a query
 * pack: the sweep prompt cites them as "the shape, not a pack" and the model discovers every
 * other statement through the StackQL discovery tools and the query library. Each file carries
 * a header comment:
 *
 *   -- id: entitlements/examples/<name>
 *   -- providers: aws, azure
 *   -- params: aws_account_id, ...
 *   -- expected_columns: cloud, principal, ...
 *   -- description: one line
 *
 * `{{ param }}` placeholders are substituted from an explicit value map, falling back to the
 * matching upper-case environment variable. A missing value fails naming the variable, except
 * in lenient mode (prompt illustration), where the placeholder is left visible.
 */

import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { EXAMPLES_DIR, QUERIES_DIR, env } from './config.ts';

const HEADER_RE = /^--\s*([a-z_]+)\s*:\s*(.*)$/;
const PARAM_RE = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;

export interface Query {
  id: string;
  path: string;
  providers: string[];
  params: string[];
  expectedColumns: string[];
  description: string;
  sql: string;
  meta: Record<string, string>;
}

function splitList(v: string | undefined): string[] {
  return (v ?? '')
    .split(',')
    .map((x) => x.trim())
    .filter((x) => x !== '');
}

export function parseQuery(text: string, filePath: string, root: string = QUERIES_DIR): Query {
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
  const rel = path.relative(root, filePath).replace(/\\/g, '/').replace(/\.sql$/, '');
  const declared = splitList(meta.params);
  return {
    id: meta.id ?? `entitlements/${rel}`,
    path: filePath,
    providers: splitList(meta.providers),
    params: declared.length ? declared : placeholdersIn(sql).sort(),
    expectedColumns: splitList(meta.expected_columns),
    description: meta.description ?? '',
    sql,
    meta,
  };
}

export function placeholdersIn(sql: string): string[] {
  return [...new Set([...sql.matchAll(PARAM_RE)].map((m) => m[1]!))];
}

let cache: Query[] | null = null;

export function listQueries(dir: string = EXAMPLES_DIR, useCache = true): Query[] {
  if (useCache && dir === EXAMPLES_DIR && cache) return cache;
  const files = readdirSync(dir)
    .filter((f) => f.endsWith('.sql'))
    .sort();
  const out = files.map((f) => {
    const p = path.join(dir, f);
    return parseQuery(readFileSync(p, 'utf8'), p);
  });
  const seen = new Set<string>();
  for (const q of out) {
    if (seen.has(q.id)) throw new Error(`duplicate query id ${q.id} under ${dir}`);
    seen.add(q.id);
  }
  if (dir === EXAMPLES_DIR) cache = out;
  return out;
}

export function loadQuery(id: string): Query {
  const q = listQueries().find((x) => x.id === id);
  if (!q) throw new Error(`no query with id '${id}' under ${EXAMPLES_DIR}`);
  return q;
}

/** Explicit values win; then the upper-cased environment variable; then fail (or keep the placeholder). */
export function renderQuery(
  q: Query,
  values: Record<string, string> = {},
  opts: { lenient?: boolean } = {},
): string {
  const resolve = (key: string): string | null => {
    const v = values[key] ?? '';
    if (v !== '') return v;
    const fromEnv = env(key.toUpperCase());
    return fromEnv !== '' ? fromEnv : null;
  };
  const missing = q.params.filter((p) => resolve(p) === null);
  if (missing.length && !opts.lenient) {
    throw new Error(
      `query ${q.id}: missing parameter${missing.length > 1 ? 's' : ''} ${missing.join(', ')} (set ${missing
        .map((m) => m.toUpperCase())
        .join(', ')} in .env or pass ${missing.length > 1 ? 'them' : 'it'} explicitly)`,
    );
  }
  return q.sql.replace(PARAM_RE, (m: string, key: string) => resolve(key) ?? m).trim();
}
