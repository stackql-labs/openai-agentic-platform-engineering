/**
 * The recertification sweep, end to end:
 *
 *   1. start one read-only StackQL MCP server holding the credentials for aws, azure, google,
 *      github and the IdP (entra_id or okta); read stackql://docs/instructions from it
 *   2. the sweep-tier agent works from prompts/sweep.md: it discovers the resources through the
 *      StackQL tools and the query library, runs SELECTs, and classifies rows into findings
 *   3. findings at or above ESCALATION_SEVERITY go to the reasoning-tier agent
 *   4. artifacts: console table + brief, runs/entitlements-<ts>.json, and the recertification
 *      report runs/entitlements-recertification-<ts>.md
 *   5. the cost and trace block
 *
 * --dry-run starts the server, prints the rendered sweep instructions and the tool list the
 * model would see, validates the example SELECTs, and exits without calling a model.
 */

import { RunContext, getAllMcpTools, run, withTrace } from '@openai/agents';
import { makeAgent, reasoningInstructions, sweepInstructions } from './agents.ts';
import { providerConfigured, requireEnv, settings, type Settings } from './config.ts';
import { RunLedger } from './costs.ts';
import { readOnlyServer, withServer } from './mcp.ts';
import { readServerInstructions } from './prompts.ts';
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
import { validateExamples } from './validate.ts';

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

function sweepInput(s: Settings): string {
  const skipped = s.providers.filter((p) => !providerConfigured(p));
  return [
    'Scenario: entitlements recertification sweep.',
    `Run started ${new Date().toISOString()}. IdP provider: ${s.idpProvider}.`,
    skipped.length ? `Providers without credentials in this run (skip them): ${skipped.join(', ')}.` : 'All providers in scope have credentials configured.',
    'Begin the sweep.',
  ].join('\n');
}

export async function dryRun(s: Settings): Promise<number> {
  let failures = 0;
  await withServer(readOnlyServer(), async (server) => {
    const serverInstructions = await readServerInstructions(server);
    const instructions = sweepInstructions(s, serverInstructions);
    console.log('\nsweep instructions (what the sweep agent receives):\n');
    console.log(instructions);
    console.log(`\n(${instructions.length} characters; server instructions ${serverInstructions ? 'included' : 'unavailable'})`);
    // the SDK applies the tool filter when it builds an agent's tools for a run (it needs the
    // agent and a run context), not in listTools(); resolve them the same way the runner does
    const sweeper = makeAgent(s.sweep, {
      name: 'entitlements-sweep',
      instructions,
      mcpServers: [server],
      outputType: FindingSetSchema,
    });
    const raw = await server.listTools();
    const tools = await getAllMcpTools({ mcpServers: [server], runContext: new RunContext(), agent: sweeper });
    const names = tools.map((t) => t.name);
    console.log(`\nserver advertises ${raw.length} tools; after the read-only filter the model sees ${names.length}: ${names.join(', ')}`);
    const forbidden = names.filter((n) => /mutation|lifecycle|pull_provider|reload_credentials/.test(n));
    if (forbidden.length) throw new Error(`read-only server exposed ${forbidden.join(', ')}`);
    console.log('\nvalidation of the example SELECTs (validate_select_query):');
    failures = await validateExamples(server, s);
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
  for (const p of s.providers) if (!providerConfigured(p)) ledger.notes.push(`${p} skipped: credentials not configured`);

  let fs: FindingSet = { scenario: 'entitlements', findings: [], summary: '' };
  let assessments: Assessment[] = [];
  let executiveSummary = '';

  await withTrace('entitlements sweep', async (trace) => {
    ledger.traceId = trace.traceId;
    await withServer(readOnlyServer('stackql-entitlements'), async (server) => {
      const serverInstructions = await readServerInstructions(server);
      if (!serverInstructions) ledger.notes.push('stackql://docs/instructions unavailable; prompts carried the local discovery briefing only');
      const sweeper = makeAgent(s.sweep, {
        name: 'entitlements-sweep',
        instructions: sweepInstructions(s, serverInstructions),
        mcpServers: [server],
        outputType: FindingSetSchema,
      });
      const r1 = await run(sweeper, sweepInput(s), { maxTurns: 80 });
      ledger.record('sweep (discover + classify)', s.sweep.model, r1);
      if (!r1.finalOutput) throw new Error('sweep agent produced no structured output');
      fs = FindingSetSchema.parse(r1.finalOutput);
      fs.scenario = 'entitlements';
      printFindings(fs);

      const candidates = escalationCandidates(fs, s.escalationSeverity);
      if (candidates.length) {
        console.log(`\nescalating ${candidates.length} finding(s) at or above '${s.escalationSeverity}' to ${s.reasoning.model}`);
        const reasoner = makeAgent(s.reasoning, {
          name: 'entitlements-reasoning',
          instructions: reasoningInstructions(s, serverInstructions),
          mcpServers: [server],
          outputType: AssessmentSetSchema,
        });
        const r2 = await run(
          reasoner,
          `Escalated findings (JSON):\n${findingsBlock(candidates)}\n\nAll findings from this sweep, for correlation:\n${findingsBlock(fs.findings)}`,
          { maxTurns: 30 },
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
