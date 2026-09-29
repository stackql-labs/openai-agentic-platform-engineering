/**
 * Run cost and trace ledger. Every run ends by printing this so the consumption economics stay
 * in frame: per step the model, requests, input / cached / output / reasoning tokens, tool calls
 * (select vs mutation) and the USD estimate from the repo-root pricing.json (OPENAI_PRICING_JSON
 * overrides or extends it; unknown model -> "n/a"), then the trace URL and the audit log path.
 */

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import type { Usage } from '@openai/agents';
import { PRICING_FILE, RUNS_DIR, env, settings } from './config.ts';
import { MUTATION_TOOLS } from './mcp.ts';
import { table, timestamp } from './text.ts';

export interface Price {
  input: number;
  cached_input?: number;
  output: number;
}

export function pricingTable(): Record<string, Price> {
  let base: Record<string, Price> = {};
  if (existsSync(PRICING_FILE)) {
    base = JSON.parse(readFileSync(PRICING_FILE, 'utf8')) as Record<string, Price>;
  }
  const override = env('OPENAI_PRICING_JSON');
  if (override !== '') Object.assign(base, JSON.parse(override) as Record<string, Price>);
  return base;
}

export function priceFor(model: string, tbl: Record<string, Price> = pricingTable()): Price | null {
  if (model in tbl) return tbl[model]!;
  // dated snapshots like gpt-5.4-mini-2026-03-17 -> gpt-5.4-mini
  const m = /^(.*)-\d{4}-\d{2}-\d{2}$/.exec(model);
  if (m && m[1]! in tbl) return tbl[m[1]!]!;
  return null;
}

export interface LedgerEntry {
  label: string;
  model: string;
  requests: number;
  input_tokens: number;
  cached_tokens: number;
  output_tokens: number;
  reasoning_tokens: number;
  tool_calls: number;
  select_calls: number;
  mutation_calls: number;
  tool_names: Record<string, number>;
  cost_usd: number | null;
}

export function costOf(e: Omit<LedgerEntry, 'cost_usd'>, tbl?: Record<string, Price>): number | null {
  const p = priceFor(e.model, tbl);
  if (!p) return null;
  const uncached = Math.max(e.input_tokens - e.cached_tokens, 0);
  const cachedRate = p.cached_input ?? p.input;
  return (uncached * p.input + e.cached_tokens * cachedRate + e.output_tokens * p.output) / 1_000_000;
}

function detailSum(details: unknown, key: string): number {
  const list = Array.isArray(details) ? details : details ? [details] : [];
  let n = 0;
  for (const d of list) {
    const v = (d as Record<string, unknown>)[key];
    if (typeof v === 'number') n += v;
  }
  return n;
}

/** The parts of an Agents SDK RunResult the ledger reads (structural, so any run result fits). */
export interface RunResultLike {
  rawResponses: { usage: Usage }[];
  newItems: { type: string; rawItem?: unknown }[];
}

export class RunLedger {
  readonly startedAt = new Date();
  traceId: string | null = null;
  entries: LedgerEntry[] = [];
  notes: string[] = [];

  constructor(readonly scenario: string) {}

  record(label: string, model: string, result: RunResultLike): LedgerEntry {
    const base = {
      label,
      model,
      requests: 0,
      input_tokens: 0,
      cached_tokens: 0,
      output_tokens: 0,
      reasoning_tokens: 0,
      tool_calls: 0,
      select_calls: 0,
      mutation_calls: 0,
      tool_names: {} as Record<string, number>,
    };
    for (const r of result.rawResponses) {
      const u = r.usage;
      base.requests += u.requests || 1;
      base.input_tokens += u.inputTokens;
      base.output_tokens += u.outputTokens;
      base.cached_tokens += detailSum(u.inputTokensDetails, 'cached_tokens');
      base.reasoning_tokens += detailSum(u.outputTokensDetails, 'reasoning_tokens');
    }
    for (const item of result.newItems) {
      if (item.type !== 'tool_call_item') continue;
      const raw = item.rawItem as { name?: unknown };
      const name = typeof raw.name === 'string' ? raw.name : '?';
      base.tool_calls += 1;
      base.tool_names[name] = (base.tool_names[name] ?? 0) + 1;
      if (name === 'run_select_query') base.select_calls += 1;
      if ((MUTATION_TOOLS as readonly string[]).includes(name)) base.mutation_calls += 1;
    }
    const entry: LedgerEntry = { ...base, cost_usd: costOf(base) };
    this.entries.push(entry);
    return entry;
  }

  get totalCostUsd(): number {
    return this.entries.reduce((a, e) => a + (e.cost_usd ?? 0), 0);
  }

  get traceUrl(): string | null {
    return this.traceId ? `https://platform.openai.com/traces/trace?trace_id=${this.traceId}` : null;
  }

  toJSON(): Record<string, unknown> {
    return {
      scenario: this.scenario,
      trace_id: this.traceId,
      trace_url: this.traceUrl,
      started_at: this.startedAt.toISOString(),
      duration_s: (Date.now() - this.startedAt.getTime()) / 1000,
      total_cost_usd: Number(this.totalCostUsd.toFixed(6)),
      entries: this.entries,
      notes: this.notes,
    };
  }

  printSummary(): void {
    const s = settings();
    const fmt = (n: number) => n.toLocaleString('en-US');
    const rows = this.entries.map((e) => [
      e.label,
      e.model,
      String(e.requests),
      fmt(e.input_tokens),
      fmt(e.cached_tokens),
      fmt(e.output_tokens),
      fmt(e.reasoning_tokens),
      `${e.tool_calls} (${e.select_calls} select, ${e.mutation_calls} mutation)`,
      e.cost_usd === null ? 'n/a' : e.cost_usd.toFixed(4),
    ]);
    const sum = (k: keyof LedgerEntry) => this.entries.reduce((a, e) => a + (e[k] as number), 0);
    rows.push([
      'total',
      '',
      String(sum('requests')),
      fmt(sum('input_tokens')),
      fmt(sum('cached_tokens')),
      fmt(sum('output_tokens')),
      fmt(sum('reasoning_tokens')),
      `${sum('tool_calls')} (${sum('select_calls')} select, ${sum('mutation_calls')} mutation)`,
      this.totalCostUsd.toFixed(4),
    ]);
    console.log(`\nrun cost and trace - ${this.scenario}`);
    console.log(
      table(['step', 'model', 'req', 'input', 'cached', 'output', 'reasoning', 'tool calls', 'USD'], rows),
    );
    const dur = (Date.now() - this.startedAt.getTime()) / 1000;
    console.log(`duration ${dur.toFixed(0)}s | tiers: sweep=${s.sweep.model} reasoning=${s.reasoning.model}`);
    const unknown = [...new Set(this.entries.filter((e) => priceFor(e.model) === null).map((e) => e.model))];
    if (unknown.length) console.log(`no pricing for ${unknown.join(', ')} - add to pricing.json or OPENAI_PRICING_JSON`);
    if (this.traceUrl) console.log(`trace: ${this.traceUrl}`);
    console.log(`stackql mcp audit log: ${s.mcpAuditLog}`);
    for (const n of this.notes) console.log(`note: ${n}`);
  }

  save(extra: Record<string, unknown>): string {
    mkdirSync(RUNS_DIR, { recursive: true });
    const p = path.join(RUNS_DIR, `${this.scenario}-${timestamp(this.startedAt)}.json`);
    writeFileSync(p, JSON.stringify({ ...this.toJSON(), ...extra }, null, 2), 'utf8');
    return p;
  }
}

