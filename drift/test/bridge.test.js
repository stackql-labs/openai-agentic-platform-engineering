import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { MODEL_HIDDEN_TOOLS, READ_ONLY, bridgeTools, launcherArgs, rowsOf } from '../src/mcp.js';
import { approvalGranted, hasDemoTag, mutationTool } from '../src/perturb.js';
import { BRIEF_SCHEMA, parseBrief } from '../src/brief.js';
import { costUsd, priceFor } from '../src/cost.js';
import { DRIFT_DIR } from '../src/env.js';

// the full tool surface of stackql-mcp 0.12.718 as listed by setup
const SERVER_TOOLS = [
  'describe_method', 'describe_resource', 'list_methods', 'list_providers', 'list_registry', 'list_resources',
  'list_services', 'pull_provider', 'query_library_get', 'query_library_search', 'reload_credentials',
  'run_lifecycle_operation', 'run_mutation_query', 'run_select_query', 'server_info', 'validate_select_query',
].map((name) => ({ name, description: name, inputSchema: { type: 'object', properties: { sql: { type: 'string' } } } }));

test('the model-facing tool bridge never includes mutation or admin tools', () => {
  const tools = bridgeTools(SERVER_TOOLS);
  const names = tools.map((t) => t.name);
  for (const hidden of MODEL_HIDDEN_TOOLS) assert.ok(!names.includes(hidden), hidden);
  assert.ok(!names.includes('run_mutation_query'));
  assert.ok(!names.includes('run_lifecycle_operation'));
  assert.ok(names.includes('run_select_query'));
  assert.ok(names.includes('validate_select_query'));
  for (const t of tools) {
    assert.equal(t.type, 'function');
    assert.equal(t.strict, false);
    assert.equal(t.parameters.type, 'object');
  }
});

test('launcher args put the model-facing server in read_only mode and reject unknown modes', () => {
  const args = launcherArgs({ mode: READ_ONLY, approot: '/tmp/approot', auditLog: '/tmp/audit.jsonl' });
  assert.equal(args[0], '--approot');
  const cfg = JSON.parse(args[3]);
  assert.equal(cfg.server.mode, 'read_only');
  assert.equal(cfg.server.transport, 'stdio');
  assert.equal(cfg.server.audit.file.path, '/tmp/audit.jsonl');
  assert.throws(() => launcherArgs({ mode: 'write', approot: '/tmp' }), /unknown server mode/);
});

test('only perturb.js opens a non read_only server', () => {
  const src = path.join(DRIFT_DIR, 'src');
  for (const f of fs.readdirSync(src)) {
    const text = fs.readFileSync(path.join(src, f), 'utf8');
    const opens = text.match(/StackqlServer\.open\(\{[^}]*mode:\s*'([a-z_]+)'/g) || [];
    for (const call of opens) {
      const mode = call.match(/mode:\s*'([a-z_]+)'/)[1];
      if (f !== 'perturb.js') assert.equal(mode, 'read_only', `${f}: ${call}`);
    }
  }
  const perturb = fs.readFileSync(path.join(src, 'perturb.js'), 'utf8');
  assert.match(perturb, /mode: 'full_access'/);
});

test('the gate refuses without the exact approval phrase or the explicit flag', () => {
  assert.equal(approvalGranted({ typed: '' }), false);
  assert.equal(approvalGranted({ typed: 'yes' }), false);
  assert.equal(approvalGranted({ typed: 'approve' }), false);
  assert.equal(approvalGranted({ typed: 'approve perturb-restore' }), false);
  assert.equal(approvalGranted({ typed: 'approve perturb' }), true);
  assert.equal(approvalGranted({ typed: '  approve perturb \n' }), true);
  assert.equal(approvalGranted({ approve: true }), true);
  assert.equal(approvalGranted({ approve: true, decline: true }), false);
  assert.equal(approvalGranted({ typed: 'approve perturb', decline: true }), false);
});

test('the demo tag assertion and the tool routing for the single mutation', () => {
  assert.equal(hasDemoTag({ purpose: 'agentic-demo' }, 'purpose', 'agentic-demo'), true);
  assert.equal(hasDemoTag({ purpose: 'prod' }, 'purpose', 'agentic-demo'), false);
  assert.equal(hasDemoTag(null, 'purpose', 'agentic-demo'), false);
  assert.equal(mutationTool("UPDATE aws.ec2.security_groups SET x = 'y' WHERE region = 'r'"), 'run_mutation_query');
  assert.equal(mutationTool('INSERT INTO azure.network.security_rules(a) SELECT 1'), 'run_mutation_query');
  assert.equal(mutationTool("EXEC aws.ec2.security_groups.revoke_security_group_ingress @region = 'r'"), 'run_lifecycle_operation');
});

test('rowsOf reads structuredContent.rows, falls back to text JSON, and raises tool errors', () => {
  assert.deepEqual(rowsOf({ structuredContent: { rows: [{ a: 1 }] } }), [{ a: 1 }]);
  assert.deepEqual(rowsOf({ content: [{ type: 'text', text: '[{"a":2}]' }] }), [{ a: 2 }]);
  assert.throws(() => rowsOf({ isError: true, content: [{ type: 'text', text: 'boom' }] }), /tool error: boom/);
});

test('the brief schema is strict and parseBrief enforces the shape', () => {
  assert.equal(BRIEF_SCHEMA.additionalProperties, false);
  assert.deepEqual(BRIEF_SCHEMA.required, ['changes', 'brief']);
  const item = BRIEF_SCHEMA.properties.changes.items;
  assert.deepEqual(item.required, Object.keys(item.properties));
  assert.deepEqual(item.properties.classification.enum, ['benign', 'material']);
  const ok = parseBrief('{"changes":[{"provider":"aws","resource_type":"security_group","resource_key":"sg-1","change":"changed","what_changed":"ingress","classification":"material","reason":"ssh open"}],"brief":"one paragraph"}');
  assert.equal(ok.changes[0].classification, 'material');
  assert.throws(() => parseBrief('{"brief":"x"}'), /schema/);
});

test('cost lookup uses exact then prefix pricing and returns null for unknown models', () => {
  const pricing = { 'gpt-x-mini': { input: 1, cached_input: 0.1, output: 4 }, 'gpt-x': { input: 2, cached_input: 0.2, output: 8 } };
  assert.equal(priceFor('gpt-x-mini', pricing).input, 1);
  assert.equal(priceFor('gpt-x-mini-2026-01-01', pricing).input, 1);
  assert.equal(priceFor('gpt-x-2026-01-01', pricing).input, 2);
  assert.equal(priceFor('other', pricing), null);
  const usd = costUsd('gpt-x-mini', { input_tokens: 1_000_000, cached_tokens: 500_000, output_tokens: 100_000 }, pricing);
  // 500k uncached at $1/M + 500k cached at $0.1/M + 100k output at $4/M
  assert.equal(Number(usd.toFixed(4)), 0.95);
  assert.equal(costUsd('other', { input_tokens: 1 }, pricing), null);
});
