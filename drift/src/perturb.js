// Operator tooling: simulate an out-of-band change so the next `run` has material drift. In real
// life this is what someone clicking in a console does; here it is one rendered StackQL statement
// per provider, executed by code (never by a model) behind an explicit approval.
//
// This is the only file that opens a server in full_access mode. The gate:
//   1. render the statements from drift/queries/perturb_*.sql and print them
//   2. require the operator to type `approve perturb` (or pass --approve; --decline proves the abort)
//   3. assert the demo tag on each target with a SELECT on the same server
//   4. execute exactly one mutation per provider
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline/promises';
import { missingProviderEnv, providerConfigured, settings } from './env.js';
import { StackqlServer, structuredOf, textOf } from './mcp.js';
import { awsTags, parseJson } from './normalize.js';
import { loadQuery, renderQuery } from './queries.js';

export const PROPOSAL_ID = 'perturb';

// Pure gate decision: true only for the exact phrase or the explicit flag. --decline always wins.
export function approvalGranted({ typed = '', approve = false, decline = false, proposalId = PROPOSAL_ID }) {
  if (decline) return false;
  if (approve) return true;
  return typed.trim() === `approve ${proposalId}`;
}

export function hasDemoTag(tags, key, value) {
  if (!tags || typeof tags !== 'object') return false;
  return String(tags[key]) === String(value);
}

// One plan item per provider: the target lookup, the tag assertion and the single mutation.
export function planFor(provider, { restore }) {
  if (provider === 'aws') {
    return {
      provider,
      target: loadQuery('perturb_aws_target'),
      mutation: loadQuery(restore ? 'perturb_aws_restore' : 'perturb_aws_open_ssh'),
      tagsOf: (row) => awsTags(row.tags),
      overridesOf: (row) => ({ group_id: row.group_id }),
      describe: restore
        ? 'revoke the tcp/22 from 0.0.0.0/0 ingress rule on the stack security group'
        : 'add an ingress rule tcp/22 from 0.0.0.0/0 to the stack security group',
    };
  }
  if (provider === 'azure') {
    return {
      provider,
      target: loadQuery('perturb_azure_target'),
      mutation: loadQuery(restore ? 'perturb_azure_restore' : 'perturb_azure_open_inbound'),
      tagsOf: (row) => parseJson(row.tags) || {},
      overridesOf: () => ({}),
      describe: restore
        ? 'delete the AllowAnyInbound rule from the stack network security group'
        : 'add an inbound Allow any/any rule from any source to the stack network security group',
    };
  }
  throw new Error(`unknown provider ${provider}`);
}

export function mutationTool(sql) {
  return /^\s*exec\b/i.test(sql) ? 'run_lifecycle_operation' : 'run_mutation_query';
}

export async function perturb({ restore = false, approve = false, decline = false, providers, input = process.stdin, log = console.log } = {}) {
  const cfg = settings();
  const wanted = providers?.length ? providers : ['aws', 'azure'];
  const plans = [];
  for (const p of wanted) {
    if (!providerConfigured(p)) {
      log(`skip ${p}: credentials not configured (${missingProviderEnv(p).join(', ')})`);
      continue;
    }
    plans.push(planFor(p, { restore }));
  }
  if (!plans.length) {
    log('no provider configured; nothing to do');
    return { executed: [], declined: false };
  }

  log(`perturb${restore ? ' --restore' : ''}: server mode full_access (this path only)`);
  const server = await StackqlServer.open({ mode: 'full_access', name: 'perturb' });
  const record = { ts: new Date().toISOString(), restore, plans: [], executed: [], declined: false };
  try {
    // 1. locate targets and assert the demo tag with a SELECT on the same server
    for (const plan of plans) {
      const sql = renderQuery(plan.target, { demo_prefix: cfg.demoPrefix });
      const rows = await server.select(sql);
      if (rows.length !== 1) {
        throw new Error(`${plan.provider}: expected exactly one target from ${plan.target.id}, got ${rows.length} (is the stack deployed?)`);
      }
      const tags = plan.tagsOf(rows[0]);
      if (!hasDemoTag(tags, cfg.demoTagKey, cfg.demoTagValue)) {
        throw new Error(
          `${plan.provider}: target is not tagged ${cfg.demoTagKey}=${cfg.demoTagValue} (tags: ${JSON.stringify(tags)}); refusing`
        );
      }
      plan.row = rows[0];
      plan.sql = renderQuery(plan.mutation, { demo_prefix: cfg.demoPrefix, ...plan.overridesOf(rows[0]) });
      plan.tool = mutationTool(plan.sql);
      record.plans.push({ provider: plan.provider, query_id: plan.mutation.id, tool: plan.tool, sql: plan.sql, target: rows[0][plan.target.expectedColumns[0]] });
    }

    // 2. print the plan
    log('');
    log(`proposal ${PROPOSAL_ID}: ${plans.length} mutation(s), one per provider`);
    for (const plan of plans) {
      log(`- ${plan.provider}: ${plan.describe}`);
      log(`  target tagged ${cfg.demoTagKey}=${cfg.demoTagValue}: ok`);
      log(`  ${plan.tool}:`);
      for (const line of plan.sql.split('\n')) log(`    ${line}`);
    }
    log('');

    // 3. gate
    let typed = '';
    if (!approve && !decline) {
      const rl = readline.createInterface({ input, output: process.stdout });
      typed = await rl.question(`type "approve ${PROPOSAL_ID}" to execute, anything else to abort: `);
      rl.close();
    }
    if (!approvalGranted({ typed, approve, decline })) {
      log('declined: nothing executed');
      record.declined = true;
      return record;
    }

    // 4. execute exactly one mutation per provider
    for (const plan of plans) {
      log(`executing ${plan.provider} via ${plan.tool} ...`);
      const res = await server.callTool(plan.tool, { sql: plan.sql, format: 'json' });
      if (res.isError) throw new Error(`${plan.provider}: ${textOf(res).slice(0, 1000)}`);
      const body = structuredOf(res);
      log(`  ${JSON.stringify(body).slice(0, 400)}`);
      record.executed.push({ provider: plan.provider, tool: plan.tool, query_id: plan.mutation.id, result: body });
    }
    log('');
    log(restore ? 'restored. The next `run` will show the change as a delta too.' : 'perturbed. Run `node drift/src/cli.js run` to see the drift; `perturb --restore` reverses it.');
    return record;
  } finally {
    await server.close();
    fs.mkdirSync(cfg.runsDir, { recursive: true });
    const file = path.join(cfg.runsDir, `drift-perturb-${record.ts.replace(/[-:]/g, '').replace(/\.\d{3}Z$/, 'Z')}.json`);
    fs.writeFileSync(file, JSON.stringify({ ...record, mutation_calls: server.calls.mutation, select_calls: server.calls.select, audit_log: cfg.auditLog }, null, 2));
    log(`record: ${file}`);
    log(`stackql audit log: ${cfg.auditLog}`);
  }
}
