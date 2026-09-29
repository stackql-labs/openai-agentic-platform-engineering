import { test } from 'node:test';
import assert from 'node:assert/strict';
import { computeDelta, deltaSql, diffAttrs } from '../src/delta.js';
import { createSnapshotTable, latestSnapshots, openDb, registerSnapshot, writeRows } from '../src/snapshot.js';
import { SOURCES } from '../src/normalize.js';

function seed(db) {
  const prev = 'snapshot_20260101T000000Z';
  const curr = 'snapshot_20260101T010000Z';
  createSnapshotTable(db, prev);
  createSnapshotTable(db, curr);
  const sg = SOURCES.snapshot_aws_security_groups;
  const nsg = SOURCES.snapshot_azure_nsgs;
  const before = [
    { group_id: 'sg-app', group_name: 'app-sg', ip_permissions: '{"item":{"ipProtocol":"tcp","fromPort":443,"toPort":443,"ipRanges":{"item":{"cidrIp":"10.0.0.0/8"}}}}', tags: '{"item":{"key":"purpose","value":"agentic-demo"}}' },
    { group_id: 'sg-gone', group_name: 'old-sg', ip_permissions: null, tags: null },
    { group_id: '', group_name: 'no-key', ip_permissions: null, tags: null },
  ];
  const after = [
    { group_id: 'sg-app', group_name: 'app-sg', ip_permissions: '{"item":[{"ipProtocol":"tcp","fromPort":443,"toPort":443,"ipRanges":{"item":{"cidrIp":"10.0.0.0/8"}}},{"ipProtocol":"tcp","fromPort":22,"toPort":22,"ipRanges":{"item":{"cidrIp":"0.0.0.0/0"}}}]}', tags: '{"item":[{"key":"purpose","value":"agentic-demo"},{"key":"aws:x","value":"ignored"}]}' },
    { group_id: 'sg-new', group_name: 'new-sg', ip_permissions: null, tags: null },
  ];
  assert.equal(writeRows(db, prev, sg, before), 2);
  assert.equal(writeRows(db, curr, sg, after), 2);
  const nsgRow = { id: '/x/nsg', name: 'nsg', security_rules: '[]', tags: '{"purpose":"agentic-demo"}' };
  writeRows(db, prev, nsg, [nsgRow]);
  writeRows(db, curr, nsg, [nsgRow]);
  registerSnapshot(db, { ts: '20260101T000000Z', table: prev, taken_at: 't', label: '', counts: { security_group: 2 }, seconds: 1, select_calls: 2 });
  registerSnapshot(db, { ts: '20260101T010000Z', table: curr, taken_at: 't', label: '', counts: { security_group: 2 }, seconds: 1, select_calls: 2 });
  return { prev, curr };
}

test('delta.sql over in-memory sqlite yields added / removed / changed and nothing for unchanged rows', () => {
  const db = openDb(':memory:');
  const { prev, curr } = seed(db);
  const deltas = computeDelta(db, prev, curr);
  const byKey = Object.fromEntries(deltas.map((d) => [`${d.change}:${d.resource_key}`, d]));
  assert.deepEqual(Object.keys(byKey).sort(), ['added:sg-new', 'changed:sg-app', 'removed:sg-gone']);
  assert.match(byKey['changed:sg-app'].what_changed, /ingress: \["tcp:443-443:10.0.0.0\/8"\] -> \["tcp:22-22:0.0.0.0\/0","tcp:443-443:10.0.0.0\/8"\]/);
  assert.equal(byKey['changed:sg-app'].provider, 'aws');
  assert.equal(byKey['added:sg-new'].before_attrs, null);
  assert.equal(byKey['removed:sg-gone'].after_attrs, null);
  // the nsg row is identical in both snapshots and must not appear
  assert.equal(deltas.filter((d) => d.resource_type === 'network_security_group').length, 0);
  db.close();
});

test('identical snapshots produce an empty delta', () => {
  const db = openDb(':memory:');
  const { curr } = seed(db);
  assert.deepEqual(computeDelta(db, curr, curr), []);
  db.close();
});

test('registry returns the two latest snapshots in ascending order', () => {
  const db = openDb(':memory:');
  seed(db);
  registerSnapshot(db, { ts: '20251231T000000Z', table: 'snapshot_20251231T000000Z', taken_at: 't', label: '', counts: {}, seconds: 0, select_calls: 0 });
  const latest = latestSnapshots(db, 2);
  assert.deepEqual(latest.map((r) => r.ts), ['20260101T000000Z', '20260101T010000Z']);
  db.close();
});

test('delta refuses table names that are not snapshot tables', () => {
  assert.throws(() => deltaSql('snapshots; DROP TABLE snapshots', 'snapshot_20260101T010000Z'), /refusing/);
  assert.throws(() => createSnapshotTable(openDb(':memory:'), 'evil'), /bad snapshot table/);
});

test('diffAttrs lists changed keys before -> after', () => {
  assert.equal(diffAttrs('{"a":1,"b":[1]}', '{"a":1,"b":[2],"c":"x"}'), 'b: [1] -> [2]; c: null -> "x"');
  assert.equal(diffAttrs(null, null), '(no attribute differences)');
});
