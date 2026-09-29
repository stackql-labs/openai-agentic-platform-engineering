import { test } from 'node:test';
import assert from 'node:assert/strict';
import { listQueries, loadQuery, parseQuery, renderForValidation, renderQuery } from '../src/queries.js';
import { SOURCES } from '../src/normalize.js';

test('parseQuery reads the header and the body', () => {
  const q = parseQuery(`-- id: drift/x
-- providers: aws, azure
-- params: aws_region, demo_prefix
-- expected_columns: a, b
-- kind: select
-- description: test
SELECT a, b FROM t WHERE region = '{{ aws_region }}' AND name = '{{ demo_prefix }}-app';
`, { id: 'drift/x' });
  assert.equal(q.id, 'drift/x');
  assert.deepEqual(q.providers, ['aws', 'azure']);
  assert.deepEqual(q.params, ['aws_region', 'demo_prefix']);
  assert.deepEqual(q.expectedColumns, ['a', 'b']);
  assert.equal(q.kind, 'select');
  assert.equal(q.sql, "SELECT a, b FROM t WHERE region = '{{ aws_region }}' AND name = '{{ demo_prefix }}-app'");
});

test('renderQuery substitutes overrides first, then upper-cased env vars, and fails fast naming the variable', () => {
  const q = loadQuery('snapshot_aws_security_groups');
  assert.equal(renderQuery(q, {}, { AWS_REGION: 'eu-west-1' }), "SELECT group_id, group_name, vpc_id, ip_permissions, tags\nFROM aws.ec2.security_groups\nWHERE region = 'eu-west-1'");
  assert.match(renderQuery(q, { aws_region: 'us-east-2' }, { AWS_REGION: 'eu-west-1' }), /us-east-2/);
  assert.throws(() => renderQuery(q, {}, {}), /missing value for \{\{ aws_region \}\} \(set AWS_REGION/);
});

test('undeclared placeholders are rejected', () => {
  const q = parseQuery('-- id: drift/y\n-- params: a\nSELECT {{ a }}, {{ b }}', { id: 'drift/y' });
  assert.throws(() => renderQuery(q, { a: '1', b: '2' }), /not declared/);
});

test('every committed query has a header, declares its placeholders and a kind', () => {
  const qs = listQueries();
  assert.ok(qs.length >= 10);
  for (const q of qs) {
    assert.match(q.id, /^drift\//, q.file);
    assert.ok(q.providers.length, `${q.id}: providers`);
    assert.ok(q.description, `${q.id}: description`);
    assert.ok(['select', 'mutation'].includes(q.kind), `${q.id}: kind`);
    for (const p of q.placeholders) assert.ok(q.params.includes(p), `${q.id}: placeholder ${p} not declared`);
    assert.doesNotThrow(() => renderForValidation(q), `${q.id}: renders with dummies`);
  }
});

test('snapshot sources are select queries with a registered normaliser; perturb mutations are marked', () => {
  for (const q of listQueries({ prefix: 'snapshot_' })) {
    assert.equal(q.kind, 'select', q.id);
    assert.ok(SOURCES[q.id.split('/').pop()], `${q.id}: normaliser`);
  }
  const mutations = listQueries().filter((q) => q.kind === 'mutation').map((q) => q.id).sort();
  assert.deepEqual(mutations, ['drift/perturb_aws_open_ssh', 'drift/perturb_aws_restore', 'drift/perturb_azure_open_inbound', 'drift/perturb_azure_restore']);
  for (const id of mutations) assert.doesNotMatch(loadQuery(id).sql, /^\s*select/i);
});
