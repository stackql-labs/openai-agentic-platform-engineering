import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { describe, it } from 'node:test';
import { PROMPTS_DIR, promptValues, settings } from '../src/config.ts';
import { buildInstructions, examplesText, loadPrompt, placeholdersIn, promptPath, renderPrompt } from '../src/prompts.ts';

function dummyEnv(): void {
  process.env.AWS_ACCESS_KEY_ID = 'x';
  process.env.AWS_SECRET_ACCESS_KEY = 'x';
  process.env.AWS_ACCOUNT_ID = '000000000000';
  process.env.AZURE_TENANT_ID = '11111111-1111-1111-1111-111111111111';
  process.env.AZURE_CLIENT_ID = 'x';
  process.env.AZURE_CLIENT_SECRET = 'x';
  process.env.AZURE_SUBSCRIPTION_ID = '00000000-0000-0000-0000-000000000000';
  process.env.GOOGLE_CREDENTIALS = '{}';
  process.env.GOOGLE_PROJECT = 'demo-project';
  process.env.STACKQL_GITHUB_USERNAME = 'x';
  process.env.STACKQL_GITHUB_PASSWORD = 'x';
  process.env.GITHUB_ORG = 'demo-org';
  process.env.DEMO_PREFIX = 'agentic-demo';
  delete process.env.IDP_PRIVILEGED_GROUP;
  delete process.env.OKTA_DOMAIN;
  delete process.env.OKTA_API_TOKEN;
}

describe('prompt files', () => {
  it('exist for every role and stay short prose without SQL', () => {
    for (const role of ['sweep', 'reasoning', 'discovery'] as const) {
      const p = promptPath(role);
      assert.ok(existsSync(p), `${p} missing`);
      const text = readFileSync(p, 'utf8');
      assert.ok(text.split('\n').length <= 70, `${role}.md is longer than ~60 lines`);
      assert.doesNotMatch(text, /\bFROM\s+[a-z_]+\.[a-z_]+\.[a-z_]+/i, `${role}.md names a provider.service.resource in SQL`);
      assert.doesNotMatch(text, /[\u2014\u2192]/, `${role}.md uses an em dash or arrow glyph`);
    }
  });

  it('the sweep prompt carries the intent and the discovery instruction, not a query pack', () => {
    const text = loadPrompt('sweep');
    for (const phrase of ['query_library_search', 'describe_method', 'validate_select_query', 'UNION ALL', 'orphan', 'leaver', 'outside collaborator']) {
      assert.ok(text.toLowerCase().includes(phrase.toLowerCase()), `sweep.md lacks '${phrase}'`);
    }
    assert.ok(placeholdersIn(text).includes('idp_provider'));
    assert.ok(placeholdersIn(text).includes('providers_in_scope'));
    assert.doesNotMatch(text, /render_query|list_queries/);
  });

  it('loadPrompt fails naming the file when a role is missing', () => {
    assert.throws(() => loadPrompt('sweep', '/nonexistent/prompts'), /\/nonexistent\/prompts\/sweep\.md not found/);
    assert.equal(promptPath('discovery'), `${PROMPTS_DIR}/discovery.md`);
  });
});

describe('prompt rendering', () => {
  it('substitutes placeholders from the values map', () => {
    const out = renderPrompt('IdP {{ idp_provider }} in {{idp_tenant}}', { idp_provider: 'okta', idp_tenant: 'Okta org x' });
    assert.equal(out, 'IdP okta in Okta org x');
  });

  it('fails naming the environment variable when a placeholder has no value', () => {
    assert.throws(
      () => renderPrompt('account {{ aws_account_id }}', {}, 'prompt sweep'),
      /prompt sweep: placeholder 'aws_account_id' has no value - set AWS_ACCOUNT_ID in \.env/,
    );
    assert.throws(() => renderPrompt('{{ a }} {{ b }}', { a: '', b: 'x' }), /'a'.*set A in/);
  });

  it('every placeholder in every prompt file has a value from the environment', () => {
    dummyEnv();
    const s = settings({ idp: 'entra_id' });
    const values = promptValues(s);
    for (const role of ['sweep', 'reasoning', 'discovery'] as const) {
      for (const k of placeholdersIn(loadPrompt(role))) assert.ok((values[k] ?? '') !== '', `${role}.md placeholder ${k} has no value`);
    }
    const sweep = buildInstructions('sweep', values, { serverInstructions: null, includeExamples: false });
    assert.ok(!sweep.includes('{{'), 'unrendered placeholder in sweep instructions');
    assert.match(sweep, /AWS account 000000000000/);
    assert.match(sweep, /Entra ID tenant 11111111-1111-1111-1111-111111111111/);
    assert.match(sweep, /agentic-demo-cloud-admins/);
    assert.match(sweep, /StackQL discovery briefing/);
  });

  it('a provider without credentials renders as not configured instead of failing', () => {
    dummyEnv();
    delete process.env.AWS_ACCOUNT_ID;
    const values = promptValues(settings({ idp: 'entra_id' }));
    assert.match(values.aws_account_id!, /not configured/);
    assert.match(values.providers_in_scope!, /AWS account: not configured/);
    const out = buildInstructions('reasoning', values, { serverInstructions: null, includeExamples: false });
    assert.match(out, /not configured - skip this provider/);
    // the sentinel never lands inside example SQL; the placeholder stays visible instead
    const ex = examplesText(values);
    assert.match(ex, /account \{\{ aws_account_id \}\}/);
    assert.doesNotMatch(ex, /not configured/);
    assert.match(ex, /\/subscriptions\/00000000-0000-0000-0000-000000000000/);
  });

  it('okta switches the IdP lines', () => {
    dummyEnv();
    process.env.OKTA_DOMAIN = 'dev-000000.okta.com';
    process.env.OKTA_API_TOKEN = 'x';
    const values = promptValues(settings({ idp: 'okta' }));
    assert.equal(values.idp_provider, 'okta');
    assert.equal(values.idp_name, 'Okta');
    assert.equal(values.okta_subdomain, 'dev-000000');
    assert.match(values.idp_tenant!, /Okta org dev-000000\.okta\.com/);
  });

  it('server instructions and examples are appended when given', () => {
    dummyEnv();
    const values = promptValues(settings({ idp: 'entra_id' }));
    const out = buildInstructions('sweep', values, { serverInstructions: '# Overview\nserver text', includeExamples: true });
    assert.match(out, /## StackQL server instructions \(stackql:\/\/docs\/instructions\)\n\n# Overview\nserver text/);
    assert.match(out, /## Example SELECTs - the shape, not a pack/);
    assert.match(out, /entitlements\/examples\/privileged_principals_all_clouds/);
    assert.match(out, /org = 'demo-org'/);
    // an example parameter with no tenancy value stays visible rather than failing the prompt
    assert.match(examplesText(values), /\{\{ aws_admin_user \}\}/);
    const without = buildInstructions('sweep', values, { serverInstructions: null, includeExamples: false });
    assert.doesNotMatch(without, /server instructions|Example SELECTs/);
  });
});
