// Snapshot: run every drift/queries/snapshot_*.sql through run_select_query on a read_only server,
// normalise the rows, and write table snapshot_<ts> plus a registry row into the local sqlite
// backend (snapshots/drift.db at the repo root). No model is involved: a snapshot costs API calls.
import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { missingProviderEnv, providerConfigured, settings } from './env.js';
import { SOURCES, stable } from './normalize.js';
import { listQueries, renderQuery } from './queries.js';

export const TS_RE = /^\d{8}T\d{6}Z$/;
export const TABLE_RE = /^snapshot_\d{8}T\d{6}Z$/;

export function snapshotTs(date = new Date()) {
  return date.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
}

export function tableName(ts) {
  if (!TS_RE.test(ts)) throw new Error(`bad snapshot timestamp ${ts}`);
  return `snapshot_${ts}`;
}

export function openDb(file = settings().snapshotDb) {
  if (file !== ':memory:') fs.mkdirSync(path.dirname(file), { recursive: true });
  const db = new DatabaseSync(file);
  db.exec(`CREATE TABLE IF NOT EXISTS snapshots (
    ts TEXT PRIMARY KEY,
    table_name TEXT NOT NULL,
    taken_at TEXT NOT NULL,
    label TEXT,
    counts_json TEXT,
    seconds REAL,
    select_calls INTEGER
  )`);
  return db;
}

export function createSnapshotTable(db, table) {
  if (!TABLE_RE.test(table)) throw new Error(`bad snapshot table name ${table}`);
  db.exec(`DROP TABLE IF EXISTS "${table}"`);
  db.exec(`CREATE TABLE "${table}" (
    provider TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    state_json TEXT,
    attrs_json TEXT NOT NULL,
    PRIMARY KEY (resource_type, resource_key)
  )`);
}

// rows -> normalised snapshot rows for one source; returns the number written
export function writeRows(db, table, source, rows) {
  if (!TABLE_RE.test(table)) throw new Error(`bad snapshot table name ${table}`);
  const ins = db.prepare(`INSERT OR REPLACE INTO "${table}" VALUES (?, ?, ?, ?, ?)`);
  let n = 0;
  for (const row of rows) {
    const key = row[source.keyColumn];
    if (key === undefined || key === null || key === '' || key === 'null') continue;
    ins.run(source.provider, source.resourceType, String(key), JSON.stringify(row), stable(source.normalise(row)));
    n += 1;
  }
  return n;
}

export function registerSnapshot(db, entry) {
  db.prepare(
    'INSERT OR REPLACE INTO snapshots (ts, table_name, taken_at, label, counts_json, seconds, select_calls) VALUES (?, ?, ?, ?, ?, ?, ?)'
  ).run(entry.ts, entry.table, entry.taken_at, entry.label || '', JSON.stringify(entry.counts), entry.seconds, entry.select_calls);
}

export function latestSnapshots(db, n = 2) {
  const rows = db.prepare('SELECT * FROM snapshots ORDER BY ts DESC LIMIT ?').all(n);
  return rows
    .reverse()
    .map((r) => ({ ...r, table: r.table_name, counts: r.counts_json ? JSON.parse(r.counts_json) : {} }));
}

export function sourceFor(query) {
  const suffix = query.id.split('/').pop();
  return SOURCES[suffix] ? { suffix, ...SOURCES[suffix] } : null;
}

export async function takeSnapshot({ server, db, label = '', log = console.log } = {}) {
  const ts = snapshotTs();
  const table = tableName(ts);
  const t0 = Date.now();
  const before = server.calls.select;
  log(`snapshot ${ts}`);
  createSnapshotTable(db, table);
  const counts = {};
  const skipped = [];
  const errors = [];
  for (const q of listQueries({ prefix: 'snapshot_' })) {
    const source = sourceFor(q);
    if (!source) {
      skipped.push(`${q.id}: no normaliser registered`);
      continue;
    }
    if (q.kind !== 'select') throw new Error(`${q.id} is not a select query`);
    const unconfigured = q.providers.filter((p) => !providerConfigured(p));
    if (unconfigured.length) {
      const missing = unconfigured.flatMap((p) => missingProviderEnv(p));
      skipped.push(`${q.id}: credentials not configured (${missing.join(', ')})`);
      log(`  skip ${q.id} (missing ${missing.join(', ')})`);
      continue;
    }
    const sql = renderQuery(q);
    try {
      const rows = await server.select(sql);
      counts[source.resourceType] = writeRows(db, table, source, rows);
      log(`  ${q.id}: ${counts[source.resourceType]} row(s)`);
    } catch (e) {
      errors.push(`${q.id}: ${e.message.slice(0, 300)}`);
      log(`  ${q.id}: error ${e.message.slice(0, 300)}`);
    }
  }
  const entry = {
    ts,
    table,
    counts,
    taken_at: new Date().toISOString(),
    label,
    seconds: (Date.now() - t0) / 1000,
    select_calls: server.calls.select - before,
    skipped,
    errors,
  };
  registerSnapshot(db, entry);
  log(`wrote ${table} (${Object.values(counts).reduce((a, b) => a + b, 0)} resources) in ${entry.seconds.toFixed(1)}s`);
  return entry;
}
