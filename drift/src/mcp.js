// StackQL MCP server wiring. The npm launcher (@stackql/mcp-server, bin stackql-mcp) spawns the
// signed stackql binary as an MCP stdio server; this module drives it with the MCP client from
// @modelcontextprotocol/sdk and bridges its tool list into Responses API function tools.
//
// Modes: every server the model can reach runs read_only (the server refuses every write). The one
// full_access server in this use case is opened by src/perturb.js, behind the approval gate.
import path from 'node:path';
import fs from 'node:fs';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { DRIFT_DIR, settings } from './env.js';

// Never bridged to a model, whatever the server mode.
export const MODEL_HIDDEN_TOOLS = Object.freeze([
  'run_mutation_query',
  'run_lifecycle_operation',
  'pull_provider',
  'reload_credentials',
]);

export const READ_ONLY = 'read_only';
export const SERVER_MODES = Object.freeze([READ_ONLY, 'safe', 'delete_safe', 'full_access']);

export function launcherCommand() {
  return path.join(DRIFT_DIR, 'node_modules', '.bin', 'stackql-mcp');
}

// Later flags win in the launcher, so passing --approot and --mcp.config overrides its defaults.
export function launcherArgs({ mode = READ_ONLY, approot, auditLog, auth }) {
  if (!SERVER_MODES.includes(mode)) throw new Error(`unknown server mode ${mode}`);
  const audit = auditLog ? { file: { path: auditLog } } : { disabled: true };
  const config = { server: { transport: 'stdio', mode, audit } };
  const args = ['--approot', approot, '--mcp.config', JSON.stringify(config)];
  // Provider auth normally comes from the process environment. `validate` passes null_auth for
  // every provider so that planning a statement never needs (or uses) a credential.
  if (auth) args.push('--auth', JSON.stringify(auth));
  return args;
}

export function nullAuth(providers) {
  return Object.fromEntries(providers.map((p) => [p, { type: 'null_auth' }]));
}

// Convert MCP tool descriptors into Responses API function tools, dropping hidden ones.
export function bridgeTools(tools, hidden = MODEL_HIDDEN_TOOLS) {
  const block = new Set(hidden);
  return tools
    .filter((t) => !block.has(t.name))
    .map((t) => ({
      type: 'function',
      name: t.name,
      description: t.description || t.name,
      parameters: t.inputSchema || { type: 'object', properties: {} },
      strict: false,
    }));
}

export function textOf(result) {
  return (result?.content || [])
    .filter((c) => c.type === 'text')
    .map((c) => c.text)
    .join('\n');
}

// run_select_query returns structuredContent.rows (format json); fall back to the text body.
export function rowsOf(result) {
  if (result?.isError) throw new Error(`tool error: ${textOf(result).slice(0, 2000)}`);
  const sc = result?.structuredContent;
  if (sc && Array.isArray(sc.rows)) return sc.rows;
  if (Array.isArray(sc)) return sc;
  const text = textOf(result).trim();
  if (text) {
    try {
      const parsed = JSON.parse(text);
      if (Array.isArray(parsed)) return parsed;
      if (parsed && Array.isArray(parsed.rows)) return parsed.rows;
    } catch {
      // not JSON
    }
  }
  if (sc && typeof sc === 'object') return [];
  throw new Error(`could not parse rows from tool result: ${text.slice(0, 500)}`);
}

export function structuredOf(result) {
  if (result?.isError) throw new Error(`tool error: ${textOf(result).slice(0, 2000)}`);
  if (result?.structuredContent) return result.structuredContent;
  const text = textOf(result).trim();
  try {
    return JSON.parse(text);
  } catch {
    return { text };
  }
}

export class StackqlServer {
  constructor({ mode = READ_ONLY, name = 'stackql', auth = null } = {}) {
    if (!SERVER_MODES.includes(mode)) throw new Error(`unknown server mode ${mode}`);
    this.mode = mode;
    this.name = name;
    this.auth = auth;
    this.client = null;
    this.transport = null;
    this.calls = { select: 0, mutation: 0, other: 0 };
    this._tools = null;
  }

  static async open(opts = {}) {
    const s = new StackqlServer(opts);
    await s.start();
    return s;
  }

  async start() {
    const cfg = settings();
    fs.mkdirSync(path.dirname(cfg.auditLog), { recursive: true });
    this.transport = new StdioClientTransport({
      command: launcherCommand(),
      args: launcherArgs({ mode: this.mode, approot: cfg.approot, auditLog: cfg.auditLog, auth: this.auth }),
      env: { ...process.env },
      stderr: 'pipe',
    });
    // The launcher and the binary log to stderr; surface only lines that look like problems.
    this.transport.stderr?.on('data', (chunk) => {
      const text = String(chunk);
      if (/error|fatal|panic|failed/i.test(text)) process.stderr.write(`[${this.name}] ${text}`);
    });
    this.client = new Client({ name: `drift-${this.name}`, version: '0.1.0' });
    await this.client.connect(this.transport);
    return this;
  }

  async listTools() {
    if (!this._tools) {
      const res = await this.client.listTools();
      this._tools = res.tools || [];
    }
    return this._tools;
  }

  async toolNames() {
    return (await this.listTools()).map((t) => t.name);
  }

  async callTool(name, args = {}) {
    if (name === 'run_select_query' || name === 'validate_select_query') this.calls.select += 1;
    else if (name === 'run_mutation_query' || name === 'run_lifecycle_operation') this.calls.mutation += 1;
    else this.calls.other += 1;
    return this.client.callTool({ name, arguments: args });
  }

  async select(sql, { rowLimit } = {}) {
    const limit = rowLimit ?? settings().rowLimit;
    const res = await this.callTool('run_select_query', { sql, format: 'json', row_limit: limit });
    return rowsOf(res);
  }

  async validate(sql) {
    const res = await this.callTool('validate_select_query', { sql, format: 'json' });
    const body = structuredOf(res);
    const valid = body.valid === true || body.valid === 'true';
    const errors = body.errors || (valid ? [] : [textOf(res).slice(0, 500)]);
    return { valid, errors: Array.isArray(errors) ? errors : [String(errors)] };
  }

  async serverInfo() {
    return structuredOf(await this.callTool('server_info', {}));
  }

  // MCP resources/read: the server publishes its instructions as stackql://docs/instructions.
  async readResource(uri) {
    const res = await this.client.readResource({ uri });
    return (res?.contents || [])
      .filter((c) => typeof c.text === 'string')
      .map((c) => c.text)
      .join('\n');
  }

  async close() {
    try {
      await this.client?.close();
    } catch {
      // already gone
    }
  }
}
