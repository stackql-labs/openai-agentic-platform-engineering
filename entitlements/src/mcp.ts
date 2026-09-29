/**
 * StackQL MCP server factory for the OpenAI Agents SDK (TypeScript).
 *
 * The server is the npm package @stackql/mcp-server: its launcher (bin/stackql-mcp.js) spawns the
 * signed stackql binary as an MCP stdio server and passes extra arguments straight through. One
 * process holds the credentials for every provider (aws, azure, google, github and the IdP) from
 * the environment it inherits, so the agent audits five control planes through one tool.
 *
 * Tool surface, verified with server_info against stackql v0.12.718:
 *
 *   discovery : list_providers, list_registry, list_services, list_resources, list_methods,
 *               describe_resource, describe_method
 *   execution : run_select_query, validate_select_query,
 *               run_mutation_query, run_lifecycle_operation        <- never exposed here
 *   library   : query_library_search, query_library_get
 *   admin     : server_info, pull_provider, reload_credentials     <- setup only, not model-facing
 *
 * Enforcement, not documentation: every model-facing server runs in `read_only` mode (the server
 * refuses every write, even a prompt-injected DELETE) and a static tool filter hides the mutation
 * and admin tools so the model never sees them. This use case has no mutation path at all - the
 * reasoning tier drafts removal statements as text for the recertification report.
 */

import { mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { MCPServerStdio, createMCPToolStaticFilter } from '@openai/agents';
import type { MCPServer, MCPToolFilterStatic } from '@openai/agents';
import { PKG_DIR, settings } from './config.ts';

export const MUTATION_TOOLS = ['run_mutation_query', 'run_lifecycle_operation'] as const;
export const ADMIN_TOOLS = ['pull_provider', 'reload_credentials'] as const;
export const READ_TOOLS = [
  'server_info',
  'list_providers',
  'list_services',
  'list_resources',
  'list_methods',
  'describe_resource',
  'describe_method',
  'run_select_query',
  'validate_select_query',
  'query_library_search',
  'query_library_get',
] as const;

export type ServerMode = 'read_only';

export interface StackqlServerOptions {
  name: string;
  command: string;
  args: string[];
  env: Record<string, string>;
  cwd: string;
  cacheToolsList: boolean;
  clientSessionTimeoutSeconds: number;
  toolFilter: MCPToolFilterStatic | undefined;
}

/** The launcher script of @stackql/mcp-server, run with the current node binary. */
export function launcherPath(): string {
  const req = createRequire(path.join(PKG_DIR, 'package.json'));
  try {
    return req.resolve('@stackql/mcp-server/bin/stackql-mcp.js');
  } catch {
    return path.join(PKG_DIR, 'node_modules', '@stackql', 'mcp-server', 'bin', 'stackql-mcp.js');
  }
}

export function serverArgs(mode: ServerMode, auditLog: string | null): string[] {
  const s = settings();
  const server: Record<string, unknown> = { transport: 'stdio', mode };
  if (auditLog) {
    mkdirSync(path.dirname(auditLog), { recursive: true });
    server.audit = { file: { path: auditLog } };
  } else {
    // without an explicit sink the server writes a log file into the cwd
    server.audit = { disabled: true };
  }
  // The launcher sets --approot and --mcp.config first; later duplicates win.
  return ['--approot', s.stackqlApproot, '--mcp.config', JSON.stringify({ server })];
}

function processEnv(): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(process.env)) if (typeof v === 'string') out[k] = v;
  return out;
}

/**
 * Options for a model-facing server: read_only mode, mutation and admin tools filtered out.
 * Pure (no process spawned) so tests can assert the invariant.
 */
export function readOnlyServerOptions(name = 'stackql-ro'): StackqlServerOptions {
  const s = settings();
  return {
    name,
    command: process.execPath,
    args: [launcherPath(), ...serverArgs('read_only', s.mcpAuditLog)],
    env: processEnv(),
    cwd: PKG_DIR,
    cacheToolsList: true,
    clientSessionTimeoutSeconds: 300,
    toolFilter: createMCPToolStaticFilter({
      allowed: [...READ_TOOLS],
      blocked: [...MUTATION_TOOLS, ...ADMIN_TOOLS],
    }),
  };
}

export function assertReadOnly(opts: StackqlServerOptions): void {
  const cfgIdx = opts.args.indexOf('--mcp.config');
  const cfg = cfgIdx >= 0 ? (JSON.parse(opts.args[cfgIdx + 1] ?? '{}') as { server?: { mode?: string } }) : {};
  if (cfg.server?.mode !== 'read_only') throw new Error('model-facing server must run in read_only mode');
  const allowed = opts.toolFilter?.allowedToolNames ?? [];
  for (const t of [...MUTATION_TOOLS, ...ADMIN_TOOLS]) {
    if (allowed.includes(t)) throw new Error(`tool ${t} cannot be exposed on a read-only server`);
    if (!(opts.toolFilter?.blockedToolNames ?? []).includes(t)) {
      throw new Error(`tool ${t} must be blocked on a read-only server`);
    }
  }
}

/** The one server every agent in this use case gets. */
export function readOnlyServer(name = 'stackql-ro'): MCPServerStdio {
  const opts = readOnlyServerOptions(name);
  assertReadOnly(opts);
  return new MCPServerStdio(opts);
}

/**
 * Setup-only server: still read_only mode, but without the tool filter so code (never a model)
 * can call pull_provider and server_info. It is not passed to any Agent.
 */
export function setupServer(name = 'stackql-setup'): MCPServerStdio {
  return new MCPServerStdio({
    name,
    command: process.execPath,
    args: [launcherPath(), ...serverArgs('read_only', null)],
    env: processEnv(),
    cwd: PKG_DIR,
    cacheToolsList: false,
    clientSessionTimeoutSeconds: 900,
  });
}

/** Call a tool from code and return its structured JSON (falls back to parsing the text). */
export async function callToolJson(
  server: MCPServer,
  name: string,
  args: Record<string, unknown>,
): Promise<{ data: unknown; text: string; isError: boolean }> {
  const res = await server.callToolResult!(name, args);
  const text = (res.content ?? [])
    .map((c) => (typeof c.text === 'string' ? c.text : ''))
    .join('\n')
    .trim();
  let data: unknown = res.structuredContent;
  if (data === undefined && text !== '') {
    try {
      data = JSON.parse(text);
    } catch {
      data = undefined;
    }
  }
  return { data, text, isError: Boolean(res.isError) };
}

export async function withServer<T>(server: MCPServerStdio, fn: (s: MCPServerStdio) => Promise<T>): Promise<T> {
  await server.connect();
  try {
    return await fn(server);
  } finally {
    await server.close();
  }
}
