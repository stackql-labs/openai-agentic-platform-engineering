import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { ADMIN_TOOLS, MUTATION_TOOLS, READ_TOOLS, assertReadOnly, readOnlyServerOptions, serverArgs } from '../src/mcp.ts';

describe('read-only StackQL MCP server', () => {
  const opts = readOnlyServerOptions('test');

  it('runs the server in read_only mode with the audit sink configured', () => {
    const i = opts.args.indexOf('--mcp.config');
    assert.ok(i >= 0);
    const cfg = JSON.parse(opts.args[i + 1]!) as { server: { mode: string; transport: string; audit: unknown } };
    assert.equal(cfg.server.mode, 'read_only');
    assert.equal(cfg.server.transport, 'stdio');
    assert.ok(cfg.server.audit);
    assert.ok(opts.args.includes('--approot'));
  });

  it('never exposes a mutation or admin tool to the model', () => {
    const allowed = opts.toolFilter?.allowedToolNames ?? [];
    const blocked = opts.toolFilter?.blockedToolNames ?? [];
    for (const t of [...MUTATION_TOOLS, ...ADMIN_TOOLS]) {
      assert.ok(!allowed.includes(t), `${t} allowed`);
      assert.ok(blocked.includes(t), `${t} not blocked`);
      assert.ok(!(READ_TOOLS as readonly string[]).includes(t), `${t} in READ_TOOLS`);
    }
    assert.ok(allowed.includes('run_select_query'));
    assert.ok(allowed.includes('validate_select_query'));
    assert.doesNotThrow(() => assertReadOnly(opts));
  });

  it('assertReadOnly rejects a tampered option set', () => {
    const tampered = { ...opts, toolFilter: { allowedToolNames: [...READ_TOOLS, 'run_mutation_query'], blockedToolNames: [] } };
    assert.throws(() => assertReadOnly(tampered), /cannot be exposed/);
    const wrongMode = { ...opts, args: serverArgs('read_only', null).map((a) => a.replace('read_only', 'full_access')) };
    assert.throws(() => assertReadOnly(wrongMode), /read_only/);
  });

  it('launches the npm package launcher with the current node binary and the full environment', () => {
    assert.equal(opts.command, process.execPath);
    assert.match(opts.args[0]!, /@stackql[\\/]mcp-server[\\/]bin[\\/]stackql-mcp\.js$/);
    assert.equal(opts.env.PATH, process.env.PATH);
    assert.equal(opts.cacheToolsList, true);
  });
});
