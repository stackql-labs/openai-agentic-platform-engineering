// Brief: the mini-tier model (SWEEP_MODEL) classifies the deltas - only the deltas, never the
// estate - with a json_schema structured output. It may call the read-only tools bridged from the
// MCP server (mutation and admin tools are filtered out in bridgeTools) to look something up, but
// the deltas are the whole input and tool use is bounded by DRIFT_MAX_TOOL_CALLS.
import OpenAI from 'openai';
import { addUsage, emptyUsage, usageOf } from './cost.js';
import { bridgeTools, textOf } from './mcp.js';

export const CHANGE_KINDS = ['added', 'removed', 'changed'];
export const CLASSIFICATIONS = ['benign', 'material'];

export const BRIEF_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['changes', 'brief'],
  properties: {
    changes: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['provider', 'resource_type', 'resource_key', 'change', 'what_changed', 'classification', 'reason'],
        properties: {
          provider: { type: 'string' },
          resource_type: { type: 'string' },
          resource_key: { type: 'string' },
          change: { type: 'string', enum: CHANGE_KINDS },
          what_changed: { type: 'string', description: 'The attribute(s) and values, before -> after' },
          classification: { type: 'string', enum: CLASSIFICATIONS },
          reason: { type: 'string' },
        },
      },
    },
    brief: { type: 'string', description: 'One paragraph in plain language for the platform team' },
  },
};

export const INSTRUCTIONS = `You are the hourly drift briefing for a demo cloud estate (AWS and Azure).
You receive only the deltas: rows that changed between two snapshots of network control-plane state
(security groups, EC2 instances, network security groups). Classify each change as benign (tag churn,
naming, versioning or other configuration with no security consequence) or material (network exposure,
privilege, encryption, anything that widens access) and give a one-sentence reason. Then write a
one-paragraph brief that cites resource ids. Do not speculate about resources that are not in the
input. You have read-only SQL tools against the providers if you need to confirm a detail; the deltas
are normally sufficient and you must not enumerate the estate.`;

export function parseBrief(text) {
  const b = JSON.parse(text);
  if (!Array.isArray(b.changes) || typeof b.brief !== 'string') throw new Error('brief did not match the schema');
  return b;
}

function finalText(response) {
  for (const item of response.output || []) {
    if (item.type !== 'message') continue;
    for (const c of item.content || []) {
      if (c.type === 'refusal') throw new Error(`model refused: ${c.refusal}`);
      if (c.type === 'output_text') return c.text;
    }
  }
  return response.output_text || '';
}

export async function briefDeltas({ deltas, server, model, effort, maxToolCalls = 6, log = console.log, client }) {
  if (!model) throw new Error('SWEEP_MODEL is not set');
  const openai = client || new OpenAI();
  const tools = bridgeTools(await server.listTools());
  const usage = { requests: 0, tokens: emptyUsage(), responseIds: [], toolCalls: {}, selectCalls: 0, mutationCalls: 0 };
  const base = {
    model,
    reasoning: { effort },
    instructions: INSTRUCTIONS,
    tools,
    text: { format: { type: 'json_schema', name: 'drift_brief', strict: true, schema: BRIEF_SCHEMA } },
    store: true,
  };
  const input = [{ role: 'user', content: JSON.stringify({ snapshot_delta: deltas }, null, 1) }];
  let response = await openai.responses.create({ ...base, input, tool_choice: 'auto' });
  let calls = 0;
  for (;;) {
    usage.requests += 1;
    usage.tokens = addUsage(usage.tokens, usageOf(response));
    usage.responseIds.push(response.id);
    const fnCalls = (response.output || []).filter((o) => o.type === 'function_call');
    if (!fnCalls.length) break;
    const outputs = [];
    for (const call of fnCalls) {
      calls += 1;
      usage.toolCalls[call.name] = (usage.toolCalls[call.name] || 0) + 1;
      if (call.name === 'run_select_query' || call.name === 'validate_select_query') usage.selectCalls += 1;
      let args = {};
      try {
        args = JSON.parse(call.arguments || '{}');
      } catch (e) {
        outputs.push({ type: 'function_call_output', call_id: call.call_id, output: `bad arguments: ${e.message}` });
        continue;
      }
      log(`  tool ${call.name} ${JSON.stringify(args).slice(0, 160)}`);
      let out;
      try {
        const res = await server.callTool(call.name, args);
        out = textOf(res) || JSON.stringify(res.structuredContent ?? {});
      } catch (e) {
        out = `tool error: ${e.message}`;
      }
      outputs.push({ type: 'function_call_output', call_id: call.call_id, output: out.slice(0, 20000) });
    }
    const exhausted = calls >= maxToolCalls;
    response = await openai.responses.create({
      ...base,
      previous_response_id: response.id,
      input: outputs,
      tool_choice: exhausted ? 'none' : 'auto',
    });
  }
  const brief = parseBrief(finalText(response));
  return { brief, usage };
}

export function printBrief(brief, log = console.log) {
  for (const c of brief.changes) {
    log(`  ${c.classification.padEnd(8)} ${c.change} ${c.provider} ${c.resource_type} ${c.resource_key}: ${c.what_changed} - ${c.reason}`);
  }
  log('');
  log(brief.brief);
}
