// Delta between two snapshot tables: plain SQL over sqlite (drift/queries/delta.sql), no cloud
// API and no model. Table names come from the registry and are checked against a strict pattern
// before being substituted into the statement.
import { loadQuery, renderQuery } from './queries.js';
import { TABLE_RE } from './snapshot.js';

export function diffAttrs(beforeJson, afterJson) {
  const b = beforeJson ? JSON.parse(beforeJson) : {};
  const a = afterJson ? JSON.parse(afterJson) : {};
  const parts = [];
  for (const k of [...new Set([...Object.keys(b), ...Object.keys(a)])].sort()) {
    const bv = JSON.stringify(b[k] ?? null);
    const av = JSON.stringify(a[k] ?? null);
    if (bv !== av) parts.push(`${k}: ${bv} -> ${av}`);
  }
  return parts.join('; ') || '(no attribute differences)';
}

export function deltaSql(prevTable, currTable) {
  for (const t of [prevTable, currTable]) {
    if (!TABLE_RE.test(t)) throw new Error(`refusing to diff non-snapshot table ${t}`);
  }
  return renderQuery(loadQuery('delta'), { prev_view: prevTable, curr_view: currTable });
}

export function computeDelta(db, prevTable, currTable) {
  const rows = db.prepare(deltaSql(prevTable, currTable)).all();
  return rows.map((r) => ({
    change: r.change,
    provider: r.provider,
    resource_type: r.resource_type,
    resource_key: r.resource_key,
    before_attrs: r.before_attrs ?? null,
    after_attrs: r.after_attrs ?? null,
    what_changed: diffAttrs(r.before_attrs, r.after_attrs),
  }));
}

export function printDelta(deltas, prev, curr, log = console.log) {
  log(`snapshot ${curr.ts} vs ${prev.ts}: ${deltas.length} change(s)`);
  for (const r of deltas) {
    log(`  ${r.change.padEnd(8)} ${r.provider} ${r.resource_type} ${r.resource_key}: ${r.what_changed.slice(0, 200)}`);
  }
}
