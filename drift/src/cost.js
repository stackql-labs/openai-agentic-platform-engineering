// Cost and trace block. Pricing comes from the repo-root pricing.json
// ({"<model id>": {"input": usd_per_1M, "cached_input": ..., "output": ...}}), extended or
// overridden by OPENAI_PRICING_JSON. Unknown model -> "n/a".
import fs from 'node:fs';
import path from 'node:path';
import { REPO_ROOT, env } from './env.js';

export function loadPricing() {
  let pricing = {};
  const file = path.join(REPO_ROOT, 'pricing.json');
  if (fs.existsSync(file)) {
    try {
      pricing = JSON.parse(fs.readFileSync(file, 'utf8'));
    } catch (e) {
      process.stderr.write(`pricing.json unreadable: ${e.message}\n`);
    }
  }
  const override = env('OPENAI_PRICING_JSON');
  if (override) {
    try {
      pricing = { ...pricing, ...JSON.parse(override) };
    } catch (e) {
      process.stderr.write(`OPENAI_PRICING_JSON unreadable: ${e.message}\n`);
    }
  }
  return pricing;
}

// Exact model id first, then the longest key the id starts with (dated snapshots of a model).
export function priceFor(model, pricing) {
  if (!model) return null;
  if (pricing[model]) return pricing[model];
  const prefix = Object.keys(pricing)
    .filter((k) => model.startsWith(k))
    .sort((a, b) => b.length - a.length)[0];
  return prefix ? pricing[prefix] : null;
}

export function costUsd(model, usage, pricing = loadPricing()) {
  const p = priceFor(model, pricing);
  if (!p) return null;
  const cached = usage.cached_tokens || 0;
  const uncached = Math.max((usage.input_tokens || 0) - cached, 0);
  const cachedRate = p.cached_input ?? p.input;
  return (uncached * p.input + cached * cachedRate + (usage.output_tokens || 0) * p.output) / 1_000_000;
}

export function usageOf(response) {
  const u = response?.usage || {};
  return {
    input_tokens: u.input_tokens || 0,
    cached_tokens: u.input_tokens_details?.cached_tokens || 0,
    output_tokens: u.output_tokens || 0,
    reasoning_tokens: u.output_tokens_details?.reasoning_tokens || 0,
  };
}

export function emptyUsage() {
  return { input_tokens: 0, cached_tokens: 0, output_tokens: 0, reasoning_tokens: 0 };
}

export function addUsage(a, b) {
  return {
    input_tokens: a.input_tokens + b.input_tokens,
    cached_tokens: a.cached_tokens + b.cached_tokens,
    output_tokens: a.output_tokens + b.output_tokens,
    reasoning_tokens: a.reasoning_tokens + b.reasoning_tokens,
  };
}

export function step({ label, model = '-', requests = 0, usage = emptyUsage(), selectCalls = 0, mutationCalls = 0, pricing }) {
  const cost = model === '-' ? 0 : costUsd(model, usage, pricing);
  return {
    label,
    model,
    requests,
    ...usage,
    select_calls: selectCalls,
    mutation_calls: mutationCalls,
    cost_usd: cost,
  };
}

function fmtUsd(v) {
  return v === null || v === undefined ? 'n/a' : `$${v.toFixed(4)}`;
}

function row(cells, widths) {
  return cells.map((c, i) => (i < 2 ? String(c).padEnd(widths[i]) : String(c).padStart(widths[i]))).join('  ');
}

export function printCostBlock({ steps, responseIds = [], auditLog, durationS, out = console.log }) {
  const header = ['step', 'model', 'req', 'input', 'cached', 'output', 'reasoning', 'tool calls', 'USD'];
  const lines = steps.map((s) => [
    s.label,
    s.model,
    s.requests,
    s.input_tokens,
    s.cached_tokens,
    s.output_tokens,
    s.reasoning_tokens,
    `${s.select_calls + s.mutation_calls} (${s.select_calls} select, ${s.mutation_calls} mutation)`,
    fmtUsd(s.cost_usd),
  ]);
  const known = steps.filter((s) => s.cost_usd !== null);
  const total = [
    'total',
    '',
    steps.reduce((n, s) => n + s.requests, 0),
    steps.reduce((n, s) => n + s.input_tokens, 0),
    steps.reduce((n, s) => n + s.cached_tokens, 0),
    steps.reduce((n, s) => n + s.output_tokens, 0),
    steps.reduce((n, s) => n + s.reasoning_tokens, 0),
    `${steps.reduce((n, s) => n + s.select_calls + s.mutation_calls, 0)} (${steps.reduce((n, s) => n + s.select_calls, 0)} select, ${steps.reduce((n, s) => n + s.mutation_calls, 0)} mutation)`,
    known.length === steps.length ? fmtUsd(known.reduce((n, s) => n + s.cost_usd, 0)) : `${fmtUsd(known.reduce((n, s) => n + s.cost_usd, 0))} (+ n/a)`,
  ];
  const all = [header, ...lines, total];
  const widths = header.map((_, i) => Math.max(...all.map((r) => String(r[i]).length)));
  out('');
  out('run cost and trace - drift');
  out(row(header, widths));
  out(widths.map((w) => '-'.repeat(w)).join('  '));
  for (const l of lines) out(row(l, widths));
  out(row(total, widths));
  if (durationS !== undefined) out(`duration ${durationS.toFixed(0)}s`);
  out(`response ids: ${responseIds.length ? responseIds.join(', ') : '(none - no model call made)'}`);
  if (auditLog) out(`stackql audit log: ${auditLog}`);
  return { total_cost_usd: known.reduce((n, s) => n + s.cost_usd, 0) };
}
