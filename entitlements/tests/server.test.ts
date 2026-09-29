/**
 * Spawns the read-only StackQL MCP server (the cached binary, no network, no provider calls) and
 * resolves its tools the way the runner does for an agent, proving the mutation and admin tools
 * are not visible to the model. Skipped when the binary has not been downloaded yet.
 */
import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { describe, it } from 'node:test';
import { Agent, RunContext, getAllMcpTools } from '@openai/agents';
import { z } from 'zod';
import { ADMIN_TOOLS, MUTATION_TOOLS, READ_TOOLS, readOnlyServer, withServer } from '../src/mcp.ts';

const cached = path.join(os.homedir(), '.stackql', 'mcp-server-bin', '0.12.718', process.platform === 'linux' ? 'linux-x64' : 'darwin-universal', 'stackql');
const available = Boolean(process.env.STACKQL_MCP_BIN) || existsSync(cached);

describe('read-only server through the SDK', { skip: !available && 'stackql binary not cached; run npm run setup first' }, () => {
  it('hides the mutation and admin tools from an agent', async () => {
    process.env.STACKQL_APPROOT ||= path.join(os.tmpdir(), 'entitlements-test-approot');
    await withServer(readOnlyServer('test-ro'), async (server) => {
      const agent = new Agent({ name: 'probe', instructions: 'none', mcpServers: [server], outputType: z.object({ ok: z.boolean() }) });
      const advertised = (await server.listTools()).map((t) => t.name);
      const visible = (await getAllMcpTools({ mcpServers: [server], runContext: new RunContext(), agent })).map((t) => t.name);
      for (const t of [...MUTATION_TOOLS, ...ADMIN_TOOLS]) {
        assert.ok(advertised.includes(t), `server advertises ${t}`);
        assert.ok(!visible.includes(t), `${t} visible to the model`);
      }
      assert.ok(visible.includes('run_select_query'));
      assert.ok(visible.every((n) => (READ_TOOLS as readonly string[]).includes(n)));
    });
  });
});
