/**
 * The recertification sweep, end to end:
 *
 *   1. start one read-only StackQL MCP server holding the credentials for aws, azure, google,
 *      github and the IdP (entra_id or okta)
 *   2. the sweep-tier agent runs the query pack (SELECT only) and classifies rows into findings
 *   3. findings at or above ESCALATION_SEVERITY go to the reasoning-tier agent
 *   4. artifacts: console table + brief, runs/entitlements-<ts>.json, and the recertification
 *      report runs/entitlements-recertification-<ts>.md
 *   5. the cost and trace block
 *
 * --dry-run starts the server, prints the query pack and the tool list the model would see,
 * validates every static SELECT with validate_select_query, and exits without calling a model.
 */

import { RunContext, getAllMcpTools, run, withTrace } from '@openai/agents';
import { buildPack, makeAgent, packText, reasoningInstructions, sweepInstructions } from './agents.ts';
import { requireEnv, settings, type Settings } from './config.ts';
import { RunLedger } from './costs.ts';
import { readOnlyServer, withServer } from './mcp.ts';
import { banner, printAssessments, printFindings, writeRecertificationReport } from './report.ts';
import {
  AssessmentSetSchema,
  FindingSetSchema,
  escalationCandidates,
  fingerprint,
  type Assessment,
  type Finding,
  type FindingSet,
} from './schemas.ts';
import { listQueriesTool, renderQueryTool } from './tools.ts';
import { validateSql } from './validate.ts';

export interface SweepOptions {
  dryRun: boolean;
  idp?: string;
}

function findingsBlock(findings: Finding[]): string {
  return JSON.stringify(
    findings.map((f) => ({ fingerprint: fingerprint(f), ...f })),
    null,
    1,
  );
}

export async function dryRun(s: Settings): Promise<number> {
  const pack = buildPack(s);
  console.log('\nquery pack (what the sweep agent receives):\n');
  console.log(packText(pack));
  let failures = 0;
  await withServer(readOnlyServer(), async (server) => {
    // the SDK applies the tool filter when it builds an agent's tools for a run (it needs the
    // agent and a run context), not in listTools(); resolve them the same way the runner does
    const sweeper = makeAgent(s.sweep, {
      name: 'entitlements-sweep',
      instructions: sweepInstructions(s),
      mcpServers: [server],
      tools: [renderQueryTool, listQueriesTool(s.idpProvider)],
      outputType: FindingSetSchema,
    });
    const raw = await server.listTools();
    const tools = await getAllMcpTools({ mcpServers: [server], runContext: new RunContext(), agent: sweeper });
    const names = tools.map((t) => t.name);
    console.log(`server advertises ${raw.length} tools; after the read-only filter the model sees ${names.length}: ${names.join(', ')}`);
    const forbidden = names.filter((n) => /mutation|lifecycle|pull_provider|reload_credentials/.test(n));
    if (forbidden.length) throw new Error(`read-only server exposed ${forbidden.join(', ')}`);
    console.log('\nvalidation of the static queries (validate_select_query per provider SELECT, sqlite shape check for CTEs):');
    for (const e of pack) {
      if (e.status !== 'static' || !e.sql) continue;
      const parts = await validateSql(server, e.sql);
      const ok = parts.every((p) => p.ok);
      if (!ok) failures += 1;
      console.log(`  ${ok ? 'pass' : 'FAIL'}  ${e.query.id}`);
      for (const p of parts) if (!p.ok) console.log(`        ${p.label}: ${p.detail}`);
    }
  });
  console.log(`\ndry run complete: no model called, nothing written${failures ? `, ${failures} validation failure(s)` : ''}`);
  return failures ? 1 : 0;
}

export async function runSweep(opts: SweepOptions): Promise<number> {
  const s = settings({ idp: opts.idp });
  banner(
    'Entitlements audit - recertification sweep',
    `always-on, read-only: SELECTs against aws, azure, google, github and ${s.idpProvider} through one StackQL MCP server`,
  );
  if (opts.dryRun) return dryRun(s);
  for (const v of ['OPENAI_API_KEY', 'SWEEP_MODEL', 'REASONING_MODEL']) requireEnv(v);

  const ledger = new RunLedger('entitlements');
  const pack = buildPack(s);
  for (const e of pack) if (e.status === 'skipped') ledger.notes.push(`${e.query.id} skipped: ${e.note}`);

  let fs: FindingSet = { scenario: 'entitlements', findings: [], summary: '' };
  let assessments: Assessment[] = [];
  let executiveSummary = '';

  await withTrace('entitlements sweep', async (trace) => {
    ledger.traceId = trace.traceId;
    await withServer(readOnlyServer('stackql-entitlements'), async (server) => {
      const sweeper = makeAgent(s.sweep, {
        name: 'entitlements-sweep',
        instructions: sweepInstructions(s),
        mcpServers: [server],
        tools: [renderQueryTool, listQueriesTool(s.idpProvider)],
        outputType: FindingSetSchema,
      });
      const r1 = await run(sweeper, `Scenario: entitlements\n\nQuery pack:\n\n${packText(pack)}`, { maxTurns: 60 });
      ledger.record('sweep (classify)', s.sweep.model, r1);
      if (!r1.finalOutput) throw new Error('sweep agent produced no structured output');
      fs = FindingSetSchema.parse(r1.finalOutput);
      fs.scenario = 'entitlements';
      printFindings(fs);

      const candidates = escalationCandidates(fs, s.escalationSeverity);
      if (candidates.length) {
        console.log(`\nescalating ${candidates.length} finding(s) at or above '${s.escalationSeverity}' to ${s.reasoning.model}`);
        const reasoner = makeAgent(s.reasoning, {
          name: 'entitlements-reasoning',
          instructions: reasoningInstructions(s),
          mcpServers: [server],
          outputType: AssessmentSetSchema,
        });
        const r2 = await run(
          reasoner,
          `Escalated findings (JSON):\n${findingsBlock(candidates)}\n\nAll findings from this sweep, for correlation:\n${findingsBlock(fs.findings)}`,
          { maxTurns: 25 },
        );
        ledger.record('reasoning (assess)', s.reasoning.model, r2);
        if (!r2.finalOutput) throw new Error('reasoning agent produced no structured output');
        const aset = AssessmentSetSchema.parse(r2.finalOutput);
        assessments = aset.assessments;
        executiveSummary = aset.executive_summary;
        printAssessments(assessments, executiveSummary);
      } else {
        console.log(`\nno findings at or above '${s.escalationSeverity}': reasoning tier not called`);
      }
    });
  });

  const reportPath = writeRecertificationReport(s, fs, assessments, executiveSummary, ledger.startedAt, ledger.traceUrl);
  console.log(`\nrecertification report: ${reportPath}`);
  const runPath = ledger.save({
    idp_provider: s.idpProvider,
    findings: fs,
    assessments,
    executive_summary: executiveSummary,
    artifacts: [{ action: 'report', path: reportPath }],
  });
  console.log(`run record: ${runPath}`);
  ledger.printSummary();
  return 0;
}
