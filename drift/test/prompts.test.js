import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { PROMPTS_DIR } from '../src/env.js';
import { composeInstructions, loadPrompt, placeholdersOf, promptFile, readServerInstructions, renderPrompt } from '../src/prompts.js';
import { promptValues } from '../src/brief.js';

const DUMMY = {
  AWS_REGION: 'eu-west-1',
  AZURE_SUBSCRIPTION_ID: '00000000-0000-0000-0000-000000000000',
  DEMO_PREFIX: 'agentic-demo',
  DEMO_TAG_KEY: 'purpose',
  DEMO_TAG_VALUE: 'agentic-demo',
  MAX_TOOL_CALLS: '6',
};

test('prompt files exist, are prose, and hold no SQL', () => {
  for (const name of ['brief', 'discovery']) {
    const file = promptFile(name);
    assert.ok(fs.existsSync(file), file);
    const text = fs.readFileSync(file, 'utf8');
    assert.ok(text.split('\n').length <= 60, `${name}.md is under ~60 lines`);
    assert.doesNotMatch(text, /^\s*select\b.*\bfrom\b/im, `${name}.md: no SELECT statement`);
    assert.doesNotMatch(text, /\b(aws|azure)\.[a-z_]+\.[a-z_]+\b/, `${name}.md: no provider.service.resource names`);
    assert.doesNotMatch(text, /[\u2014\u2192]/, `${name}.md: ASCII punctuation only`);
  }
  assert.throws(() => promptFile('nope'), /prompt nope: file not found/);
});

test('brief.md declares the placeholders the code fills and renders with dummy env', () => {
  const text = fs.readFileSync(path.join(PROMPTS_DIR, 'brief.md'), 'utf8');
  assert.deepEqual(placeholdersOf(text).sort(), ['aws_region', 'azure_subscription_id', 'demo_prefix', 'demo_tag_key', 'demo_tag_value', 'max_tool_calls']);
  const rendered = loadPrompt('brief', {}, DUMMY);
  assert.match(rendered, /region eu-west-1/);
  assert.match(rendered, /at most 6 tool calls/);
  assert.match(rendered, /`purpose=agentic-demo`/);
  assert.doesNotMatch(rendered, /\{\{/);
  assert.doesNotMatch(loadPrompt('discovery', {}, DUMMY), /\{\{/);
});

test('missing placeholder values fail naming the variable; overrides win over env', () => {
  assert.throws(() => loadPrompt('brief', {}, { ...DUMMY, AWS_REGION: '' }), /prompt brief: missing value for \{\{ aws_region \}\} \(set AWS_REGION/);
  assert.throws(() => renderPrompt('x {{ thing }}', {}, {}, { name: 'y' }), /prompt y: missing value for \{\{ thing \}\} \(set THING/);
  assert.equal(renderPrompt('r={{ aws_region }}', { aws_region: 'us-east-2' }, DUMMY), 'r=us-east-2');
  assert.equal(renderPrompt('r={{ aws_region }}', {}, DUMMY), 'r=eu-west-1');
});

test('promptValues fills tenancy and policy from settings and never leaves a provider blank', () => {
  const v = promptValues({ awsRegion: '', azureSubscriptionId: 'sub', demoPrefix: 'p', demoTagKey: 'k', demoTagValue: 'v', maxToolCalls: 3 });
  assert.equal(v.aws_region, 'not configured');
  assert.equal(v.azure_subscription_id, 'sub');
  assert.equal(v.max_tool_calls, '3');
  assert.doesNotThrow(() => loadPrompt('brief', v, {}));
});

test('composeInstructions appends the server instructions when present and the read failure is non-fatal', async () => {
  const both = composeInstructions({ role: 'ROLE\n', discovery: 'DISCOVERY', serverInstructions: '# Overview\nserver text' });
  assert.equal(both, 'ROLE\n\nDISCOVERY\n\n# Overview\nserver text');
  assert.equal(composeInstructions({ role: 'ROLE', discovery: 'DISCOVERY', serverInstructions: '' }), 'ROLE\n\nDISCOVERY');
  const notes = [];
  const text = await readServerInstructions({ readResource: async () => { throw new Error('no such resource'); } }, { log: (m) => notes.push(m) });
  assert.equal(text, '');
  assert.match(notes[0], /could not read stackql:\/\/docs\/instructions \(no such resource\)/);
  const ok = await readServerInstructions({ readResource: async (uri) => `read ${uri}` }, { log: () => {} });
  assert.equal(ok, 'read stackql://docs/instructions');
});
