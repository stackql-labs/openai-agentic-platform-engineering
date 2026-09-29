/**
 * `setup`: pull the providers this use case queries into the StackQL approot through the MCP
 * tool pull_provider (one call per provider), then print server_info. Nothing beyond npm and the
 * @stackql/mcp-server package is needed. This is code calling the server, not a model: the
 * setup server carries no tool filter but still runs in read_only mode.
 */

import { settings } from './config.ts';
import { callToolJson, setupServer, withServer } from './mcp.ts';

export async function runSetup(opts: { idp?: string }): Promise<number> {
  const s = settings({ idp: opts.idp });
  console.log(`stackql approot: ${s.stackqlApproot}`);
  console.log(`providers: ${s.providers.join(', ')} (IdP: ${s.idpProvider})`);
  let failures = 0;
  await withServer(setupServer(), async (server) => {
    for (const p of s.providers) {
      process.stdout.write(`pull_provider ${p} ... `);
      const r = await callToolJson(server, 'pull_provider', { provider: p, format: 'json' });
      if (r.isError) {
        failures += 1;
        console.log(`FAILED\n  ${r.text.slice(0, 300)}`);
      } else {
        const d = (r.data ?? {}) as Record<string, unknown>;
        console.log(`ok${d.version ? ` (${String(d.version)})` : ''}`);
      }
    }
    const info = await callToolJson(server, 'server_info', { format: 'json' });
    console.log('\nserver_info:');
    console.log(info.data ? JSON.stringify(info.data, null, 2) : info.text);
  });
  return failures ? 1 : 0;
}
