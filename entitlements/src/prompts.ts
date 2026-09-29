/**
 * Prompt loader. Every agent role reads its instructions from entitlements/prompts/<role>.md:
 * prose that states the intent, the scope and the output contract, and leaves resource and
 * column discovery to the model through the StackQL tools. `{{ placeholder }}` values come from
 * the environment (config.promptValues); a placeholder without a value fails fast naming the
 * variable to set.
 *
 * Assembly of the instructions an agent receives:
 *
 *   prompts/<role>.md          the role prompt (sweep or reasoning)
 *   prompts/discovery.md       the shared discovery briefing
 *   stackql://docs/instructions  the server's own instructions, read at startup over MCP
 *   queries/examples/*.sql     (sweep only) two illustrative SELECTs, "the shape, not a pack"
 */

import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import type { MCPServerStdio } from '@openai/agents';
import { NOT_CONFIGURED, PROMPTS_DIR, ConfigError } from './config.ts';
import { listQueries, renderQuery } from './queries.ts';

export const PLACEHOLDER_RE = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;
export const SERVER_INSTRUCTIONS_URI = 'stackql://docs/instructions';

export type PromptRole = 'sweep' | 'reasoning' | 'discovery';

export function promptPath(role: PromptRole, dir: string = PROMPTS_DIR): string {
  return path.join(dir, `${role}.md`);
}

export function loadPrompt(role: PromptRole, dir: string = PROMPTS_DIR): string {
  const p = promptPath(role, dir);
  if (!existsSync(p)) throw new ConfigError(`prompt file ${p} not found`);
  return readFileSync(p, 'utf8');
}

export function placeholdersIn(text: string): string[] {
  return [...new Set([...text.matchAll(PLACEHOLDER_RE)].map((m) => m[1]!))];
}

/** Substitute placeholders; a missing or empty value fails naming the environment variable. */
export function renderPrompt(text: string, values: Record<string, string>, label = 'prompt'): string {
  const missing = placeholdersIn(text).filter((k) => (values[k] ?? '') === '');
  if (missing.length) {
    throw new ConfigError(
      `${label}: placeholder${missing.length > 1 ? 's' : ''} ${missing.map((m) => `'${m}'`).join(', ')} ` +
        `ha${missing.length > 1 ? 've' : 's'} no value - set ${missing.map((m) => m.toUpperCase()).join(', ')} in .env`,
    );
  }
  return text.replace(PLACEHOLDER_RE, (_m, key: string) => values[key]!).trim();
}

/**
 * Read the server's own instructions resource. Returns null (and says why) when the server
 * does not publish it or the read fails, so a run continues without it.
 */
export async function readServerInstructions(server: MCPServerStdio): Promise<string | null> {
  try {
    const res = await server.readResource(SERVER_INSTRUCTIONS_URI);
    const text = (res.contents ?? [])
      .map((c) => ('text' in c && typeof c.text === 'string' ? c.text : ''))
      .filter((t) => t !== '')
      .join('\n')
      .trim();
    if (text === '') {
      console.log(`note: ${SERVER_INSTRUCTIONS_URI} returned no text; continuing with the local discovery briefing only`);
      return null;
    }
    return text;
  } catch (e) {
    console.log(`note: could not read ${SERVER_INSTRUCTIONS_URI} (${(e as Error).message}); continuing with the local discovery briefing only`);
    return null;
  }
}

/**
 * The example SELECTs rendered for the prompt. A placeholder without a value, or whose provider
 * is not configured, stays visible as `{{ name }}` rather than rendering the sentinel into SQL.
 */
export function examplesText(values: Record<string, string>): string {
  const usable = Object.fromEntries(Object.entries(values).filter(([, v]) => v !== NOT_CONFIGURED));
  const parts = listQueries().map((q) => {
    const sql = renderQuery(q, usable, { lenient: true });
    return `### ${q.id}\n${q.description}\n\`\`\`sql\n${sql}\n\`\`\``;
  });
  if (!parts.length) return '';
  return [
    '## Example SELECTs - the shape, not a pack',
    'Two illustrative statements from entitlements/queries/examples/. Discover everything else',
    'yourself; adapt these only where the intent matches. Placeholders are filled with this',
    "run's tenancy where a value exists.",
    '',
    ...parts,
  ].join('\n');
}

export interface InstructionExtras {
  serverInstructions: string | null;
  includeExamples: boolean;
}

/** Role prompt + discovery briefing + server instructions (+ examples for the sweep). */
export function buildInstructions(
  role: 'sweep' | 'reasoning',
  values: Record<string, string>,
  extras: InstructionExtras,
): string {
  const sections = [
    renderPrompt(loadPrompt(role), values, `prompt ${role}`),
    renderPrompt(loadPrompt('discovery'), values, 'prompt discovery'),
  ];
  if (extras.serverInstructions) {
    sections.push(`## StackQL server instructions (${SERVER_INSTRUCTIONS_URI})\n\n${extras.serverInstructions}`);
  }
  if (extras.includeExamples) {
    const ex = examplesText(values);
    if (ex) sections.push(ex);
  }
  return sections.join('\n\n');
}
