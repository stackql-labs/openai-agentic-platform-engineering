/**
 * Agent definitions for the two model tiers.
 *
 *   sweep     (SWEEP_MODEL)     works from prompts/sweep.md: discovers the resources for the
 *                               four recertification questions through the StackQL tools and
 *                               classifies rows into the shared findings shape
 *   reasoning (REASONING_MODEL) works from prompts/reasoning.md: receives findings at or above
 *                               ESCALATION_SEVERITY and says who should confirm each, the
 *                               least-privilege alternative and the removal statement whose
 *                               contract it discovered (never executed)
 *
 * Both get the discovery briefing (prompts/discovery.md) and the server's own instructions
 * (stackql://docs/instructions) appended. Model ids and reasoning effort come from the
 * environment; nothing is hardcoded. The agents hold no function tools: the StackQL MCP server
 * is the whole tool surface.
 */

import { Agent } from '@openai/agents';
import type { MCPServer, ModelSettings } from '@openai/agents';
import type { ZodType } from 'zod';
import { promptValues, type ModelTier, type Settings } from './config.ts';
import { buildInstructions } from './prompts.ts';

export function sweepInstructions(s: Settings, serverInstructions: string | null): string {
  return buildInstructions('sweep', promptValues(s), { serverInstructions, includeExamples: true });
}

export function reasoningInstructions(s: Settings, serverInstructions: string | null): string {
  return buildInstructions('reasoning', promptValues(s), { serverInstructions, includeExamples: false });
}

const EFFORTS = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] as const;
type Effort = (typeof EFFORTS)[number];

export function modelSettingsFor(t: ModelTier): ModelSettings {
  if (!(EFFORTS as readonly string[]).includes(t.reasoningEffort)) {
    throw new Error(
      `${t.name.toUpperCase()}_REASONING_EFFORT must be one of ${EFFORTS.join('|')}, got '${t.reasoningEffort}'`,
    );
  }
  return { reasoning: { effort: t.reasoningEffort as Effort }, parallelToolCalls: true };
}

export function makeAgent<T extends ZodType>(
  t: ModelTier,
  opts: { name: string; instructions: string; mcpServers: MCPServer[]; outputType: T },
): Agent<unknown, T> {
  return new Agent({
    name: opts.name,
    instructions: opts.instructions,
    model: t.model,
    modelSettings: modelSettingsFor(t),
    mcpServers: opts.mcpServers,
    outputType: opts.outputType,
  });
}
