#!/usr/bin/env node
// drift - always-on drift detection over AWS and Azure network control-plane state.
//
//   node drift/src/cli.js setup                 pull providers, print server_info
//   node drift/src/cli.js snapshot [--label x]  take one snapshot (API calls only, no model)
//   node drift/src/cli.js run [--no-snapshot]   snapshot -> delta (SQL) -> brief on deltas only
//   node drift/src/cli.js validate              validate_select_query for every SELECT
//   node drift/src/cli.js perturb [--restore] [--approve|--decline] [--provider aws|azure]
import fs from 'node:fs';
import path from 'node:path';
import { REPO_ROOT, STACK_DIR, settings } from './env.js';
import { StackqlServer, MODEL_HIDDEN_TOOLS, nullAuth } from './mcp.js';
import { listQueries, renderForValidation } from './queries.js';
import { latestSnapshots, openDb, takeSnapshot } from './snapshot.js';
import { computeDelta, printDelta } from './delta.js';
import { briefDeltas, printBrief } from './brief.js';
import { loadPricing, printCostBlock, step } from './cost.js';
import { perturb } from './perturb.js';

const PROVIDERS = ['aws', 'azure'];

const HELP = `drift - drift detection with the StackQL MCP server and the OpenAI Responses API

usage: node drift/src/cli.js <command> [flags]

commands
  setup                    pull the aws and azure providers into STACKQL_APPROOT and print server_info
  snapshot [--label text]  snapshot the estate into snapshots/drift.db (API calls only, no model call)
  run [--no-snapshot]      take a snapshot, diff it against the previous one in SQL, and brief on the
      [--label text]         deltas with SWEEP_MODEL from the intent prompt drift/prompts/brief.md (plus
                           drift/prompts/discovery.md and the server's stackql://docs/instructions);
                           no deltas -> one line and no model call
  validate                 run validate_select_query for every code-owned SELECT in drift/queries and the stack anchors
  perturb [--restore]      operator tooling: simulate an out-of-band change on the stack's security
      [--approve|--decline]  group (aws) and network security group (azure) through an approval gate;
      [--provider p]         --restore reverses it. The only path that opens a full_access server.
  --help                   this text

reads the repo-root .env (OPENAI_API_KEY, SWEEP_MODEL, provider credentials, AWS_REGION,
AZURE_SUBSCRIPTION_ID, DEMO_PREFIX, DEMO_TAG_KEY, DEMO_TAG_VALUE, STACKQL_APPROOT, ...)`;

function parseArgs(argv) {
  const out = { command: null, flags: {}, list: {} };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--help' || a === '-h') out.flags.help = true;
    else if (a === '--no-snapshot') out.flags.noSnapshot = true;
    else if (a === '--restore') out.flags.restore = true;
    else if (a === '--approve') out.flags.approve = true;
    else if (a === '--decline') out.flags.decline = true;
    else if (a === '--label') out.flags.label = argv[++i] || '';
    else if (a === '--provider') (out.list.providers ||= []).push(argv[++i]);
    else if (a.startsWith('-')) throw new Error(`unknown flag ${a}`);
    else if (!out.command) out.command = a;
    else throw new Error(`unexpected argument ${a}`);
  }
  return out;
}

function tsForFile(d = new Date()) {
  return d.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z');
}

function writeRun(cfg, name, payload) {
  fs.mkdirSync(cfg.runsDir, { recursive: true });
  const file = path.join(cfg.runsDir, `${name}.json`);
  fs.writeFileSync(file, JSON.stringify(payload, null, 2));
  return file;
}

async function cmdSetup() {
  const cfg = settings();
  console.log(`approot: ${cfg.approot}`);
  const server = await StackqlServer.open({ mode: 'read_only', name: 'setup' });
  try {
    for (const p of PROVIDERS) {
      process.stdout.write(`pull_provider ${p} ... `);
      const res = await server.callTool('pull_provider', { provider: p, format: 'json' });
      const body = res.structuredContent ?? {};
      console.log(res.isError ? `error: ${(res.content?.[0]?.text || '').slice(0, 300)}` : (body.message || body.status || JSON.stringify(body).slice(0, 200) || 'ok'));
    }
    const info = await server.serverInfo();
    console.log('server_info:');
    console.log(JSON.stringify(info, null, 2));
    const names = await server.toolNames();
    console.log(`tools (${names.length}): ${names.join(', ')}`);
    console.log(`hidden from the model in run: ${MODEL_HIDDEN_TOOLS.join(', ')}`);
  } finally {
    await server.close();
  }
}

async function cmdSnapshot({ label }) {
  const cfg = settings();
  const server = await StackqlServer.open({ mode: 'read_only', name: 'snapshot' });
  const db = openDb(cfg.snapshotDb);
  try {
    const entry = await takeSnapshot({ server, db, label });
    console.log(`registry: ${cfg.snapshotDb} (${latestSnapshots(db, 1000).length} snapshot(s))`);
    return entry;
  } finally {
    db.close();
    await server.close();
  }
}

async function cmdRun({ noSnapshot, label }) {
  const cfg = settings();
  const t0 = Date.now();
  const pricing = loadPricing();
  const steps = [];
  const responseIds = [];
  const db = openDb(cfg.snapshotDb);
  let server = null;
  const runName = `drift-${tsForFile()}`;
  const payload = { run: runName, started_at: new Date().toISOString(), snapshot: null, prev: null, curr: null, deltas: [], brief: null, model_called: false };
  try {
    if (!noSnapshot) {
      server = await StackqlServer.open({ mode: 'read_only', name: 'run' });
      payload.snapshot = await takeSnapshot({ server, db, label: label || 'run' });
      steps.push(step({ label: 'snapshot (no model)', selectCalls: payload.snapshot.select_calls, pricing }));
    }
    const reg = latestSnapshots(db, 2);
    if (reg.length < 2) {
      console.log(`only one snapshot exists (${reg[0]?.ts || 'none'}): run again later to see deltas`);
      steps.push(step({ label: 'delta (SQL, no model)', pricing }));
      finish();
      return 0;
    }
    const [prev, curr] = reg;
    payload.prev = { ts: prev.ts, table: prev.table, counts: prev.counts };
    payload.curr = { ts: curr.ts, table: curr.table, counts: curr.counts };
    const deltas = computeDelta(db, prev.table, curr.table);
    payload.deltas = deltas;
    console.log('');
    printDelta(deltas, prev, curr);
    steps.push(step({ label: 'delta (SQL, no model)', pricing }));
    if (!deltas.length) {
      console.log(`no changes between ${prev.ts} and ${curr.ts}: nothing to brief, no model call made`);
      finish();
      return 0;
    }
    if (!cfg.sweepModel) throw new Error('SWEEP_MODEL is not set (the mini-tier model id, in .env)');
    if (!server) server = await StackqlServer.open({ mode: 'read_only', name: 'run' });
    console.log('');
    console.log(`brief: ${deltas.length} delta(s) -> ${cfg.sweepModel} (effort ${cfg.sweepEffort})`);
    const selectBefore = server.calls.select;
    const { brief, usage } = await briefDeltas({
      deltas: deltas.map(({ change, provider, resource_type, resource_key, what_changed }) => ({ change, provider, resource_type, resource_key, what_changed })),
      server,
      model: cfg.sweepModel,
      effort: cfg.sweepEffort,
      maxToolCalls: cfg.maxToolCalls,
    });
    payload.brief = brief;
    payload.model_called = true;
    responseIds.push(...usage.responseIds);
    steps.push(step({
      label: 'brief (deltas only)',
      model: cfg.sweepModel,
      requests: usage.requests,
      usage: usage.tokens,
      selectCalls: server.calls.select - selectBefore,
      mutationCalls: 0,
      pricing,
    }));
    console.log('');
    printBrief(brief);
    finish();
    return 0;
  } finally {
    db.close();
    if (server) await server.close();
  }

  function finish() {
    const durationS = (Date.now() - t0) / 1000;
    const { total_cost_usd } = printCostBlock({ steps, responseIds, auditLog: cfg.auditLog, durationS });
    payload.steps = steps;
    payload.response_ids = responseIds;
    payload.total_cost_usd = total_cost_usd;
    payload.duration_s = durationS;
    payload.audit_log = cfg.auditLog;
    const file = writeRun(cfg, runName, payload);
    console.log(`record: ${file}`);
  }
}

// Every SELECT under drift/queries plus the SELECT anchors of the stacks, through validate_select_query.
function stackSelects() {
  const out = [];
  if (!fs.existsSync(STACK_DIR)) return out;
  for (const provider of fs.readdirSync(STACK_DIR)) {
    const dir = path.join(STACK_DIR, provider, 'resources');
    if (!fs.existsSync(dir)) continue;
    for (const f of fs.readdirSync(dir).filter((x) => x.endsWith('.iql')).sort()) {
      const text = fs.readFileSync(path.join(dir, f), 'utf8');
      const parts = text.split(/\/\*\+\s*([^*]+?)\s*\*\//);
      for (let i = 1; i < parts.length; i += 2) {
        const anchor = parts[i].split(',')[0].trim();
        const sql = parts[i + 1].trim().replace(/;\s*$/, '');
        if (/^\s*select\b/i.test(sql)) {
          out.push({ id: `stack/${provider}/${f}#${anchor}`, sql });
        }
      }
    }
  }
  return out;
}

async function cmdValidate() {
  // null_auth for every provider: validate_select_query plans the statement and never calls a provider.
  const server = await StackqlServer.open({ mode: 'read_only', name: 'validate', auth: nullAuth(PROVIDERS) });
  const results = [];
  try {
    for (const q of listQueries()) {
      if (q.kind !== 'select') {
        results.push({ id: q.id, status: 'skipped (mutation - executed only through the perturb gate)' });
        continue;
      }
      if (q.providers.includes('local')) {
        results.push({ id: q.id, status: 'skipped (local sqlite SQL - covered by drift/test/delta.test.js)' });
        continue;
      }
      const sql = renderForValidation(q);
      const r = await server.validate(sql);
      results.push({ id: q.id, status: r.valid ? 'pass' : `FAIL ${r.errors.join('; ').slice(0, 300)}` });
    }
    for (const s of stackSelects()) {
      // stack anchors are Jinja templates: plain names get a dummy, `x | ... | length` gets a number
      const sql = s.sql.replace(/\{\{\s*([A-Za-z0-9_]+)([^}]*)\}\}/g, (_, k, filters) => {
        if (/\blength\b/.test(filters)) return '1';
        if (k === 'region') return 'us-east-1';
        if (k === 'subscription_id') return '00000000-0000-0000-0000-000000000000';
        return `validate-${k}`;
      });
      const r = await server.validate(sql);
      results.push({ id: s.id, status: r.valid ? 'pass' : `FAIL ${r.errors.join('; ').slice(0, 300)}` });
    }
  } finally {
    await server.close();
  }
  for (const r of results) console.log(`${r.status.startsWith('FAIL') ? 'FAIL' : r.status.startsWith('pass') ? 'pass' : 'skip'}  ${r.id}${r.status.startsWith('FAIL') ? `  ${r.status}` : ''}`);
  const failed = results.filter((r) => r.status.startsWith('FAIL'));
  console.log(`${results.length - failed.length}/${results.length} ok`);
  return failed.length ? 1 : 0;
}

async function main() {
  const { command, flags, list } = parseArgs(process.argv.slice(2));
  if (flags.help || !command) {
    console.log(HELP);
    return flags.help ? 0 : 1;
  }
  switch (command) {
    case 'setup':
      await cmdSetup();
      return 0;
    case 'snapshot':
      await cmdSnapshot({ label: flags.label || '' });
      return 0;
    case 'run':
      return cmdRun({ noSnapshot: !!flags.noSnapshot, label: flags.label });
    case 'validate':
      return cmdValidate();
    case 'perturb': {
      if (flags.approve && flags.decline) throw new Error('--approve and --decline are mutually exclusive');
      const r = await perturb({ restore: !!flags.restore, approve: !!flags.approve, decline: !!flags.decline, providers: list.providers });
      return r.declined ? 2 : 0;
    }
    default:
      console.error(`unknown command ${command}\n`);
      console.log(HELP);
      return 1;
  }
}

main()
  .then((code) => process.exit(code))
  .catch((e) => {
    console.error(`drift: ${e.message}`);
    if (process.env.DRIFT_DEBUG) console.error(e.stack);
    process.exit(1);
  });

export { REPO_ROOT };
