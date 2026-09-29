// Query loader for the code-owned SQL in drift/queries/: the snapshot sources (a mechanical job
// with no model), the local delta over sqlite, and the perturb target/mutation statements (operator
// tooling). The model never sees these; it works from drift/prompts/ and discovers resources at run
// time. One file per statement with a header comment:
//
//   -- id: drift/snapshot_aws_security_groups
//   -- providers: aws
//   -- params: aws_region
//   -- expected_columns: group_id, group_name, vpc_id, ip_permissions, tags
//   -- kind: select            (select | mutation; mutations run only through the perturb gate)
//   -- description: one line
//   <SQL with {{ param }} placeholders>
//
// Placeholders are filled from explicit overrides first, then from the matching upper-cased
// environment variable (aws_region -> AWS_REGION). A missing value fails fast naming the variable.
import fs from 'node:fs';
import path from 'node:path';
import { QUERIES_DIR } from './env.js';

const HEADER_RE = /^--\s*([a-z_]+)\s*:\s*(.*)$/;
const PARAM_RE = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;

function splitList(v) {
  return (v || '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
}

export function parseQuery(text, { id = '', file = '' } = {}) {
  const meta = {};
  const sqlLines = [];
  let inHeader = true;
  for (const line of text.split(/\r?\n/)) {
    const m = inHeader ? line.match(HEADER_RE) : null;
    if (m) {
      meta[m[1]] = m[2].trim();
    } else {
      if (line.trim() !== '' || !inHeader) inHeader = false;
      if (!inHeader) sqlLines.push(line);
    }
  }
  const sql = sqlLines.join('\n').trim().replace(/;\s*$/, '');
  if (!sql) throw new Error(`query ${id || file}: no SQL body`);
  const placeholders = [...new Set([...sql.matchAll(PARAM_RE)].map((m) => m[1]))];
  const kind = (meta.kind || (/^\s*select\b/i.test(sql) ? 'select' : 'mutation')).toLowerCase();
  return {
    id: meta.id || id,
    file,
    providers: splitList(meta.providers),
    params: splitList(meta.params),
    expectedColumns: splitList(meta.expected_columns),
    description: meta.description || '',
    kind,
    sql,
    placeholders,
    meta,
  };
}

export function queryFiles(dir = QUERIES_DIR) {
  return fs
    .readdirSync(dir)
    .filter((f) => f.endsWith('.sql'))
    .sort()
    .map((f) => path.join(dir, f));
}

export function loadQueryFile(file) {
  const base = path.basename(file, '.sql');
  return parseQuery(fs.readFileSync(file, 'utf8'), { id: `drift/${base}`, file });
}

export function listQueries({ prefix = '', dir = QUERIES_DIR } = {}) {
  return queryFiles(dir)
    .filter((f) => path.basename(f).startsWith(prefix))
    .map(loadQueryFile);
}

export function loadQuery(id, dir = QUERIES_DIR) {
  const base = id.replace(/^drift\//, '');
  const file = path.join(dir, `${base}.sql`);
  if (!fs.existsSync(file)) throw new Error(`query ${id}: file not found (${file})`);
  return loadQueryFile(file);
}

export function renderQuery(q, overrides = {}, source = process.env) {
  const values = {};
  for (const p of new Set([...q.params, ...q.placeholders])) {
    const fromOverride = overrides[p];
    const fromEnv = source[p.toUpperCase()];
    const v = fromOverride !== undefined && fromOverride !== null ? String(fromOverride) : fromEnv;
    if (v === undefined || v === '') {
      throw new Error(
        `query ${q.id}: missing value for {{ ${p} }} (set ${p.toUpperCase()} in .env or pass an override)`
      );
    }
    values[p] = v;
  }
  const undeclared = q.placeholders.filter((p) => !q.params.includes(p));
  if (undeclared.length) {
    throw new Error(`query ${q.id}: placeholders not declared in the params header: ${undeclared.join(', ')}`);
  }
  return q.sql.replace(PARAM_RE, (_, key) => values[key]);
}

// For validate_select_query without real tenancy values: fill anything unset with a dummy.
export function renderForValidation(q, overrides = {}) {
  const dummies = {};
  for (const p of q.params) dummies[p] = `validate-${p}`;
  if (q.params.includes('aws_region')) dummies.aws_region = 'us-east-1';
  if (q.params.includes('azure_subscription_id')) {
    dummies.azure_subscription_id = '00000000-0000-0000-0000-000000000000';
  }
  return renderQuery(q, { ...dummies, ...overrides }, {});
}
