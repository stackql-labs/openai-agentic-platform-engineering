// Prompt loader. The model's instructions are prose in drift/prompts/*.md, one file per role
// (brief.md) plus the shared discovery briefing (discovery.md). Code reads the file, substitutes
// {{ placeholder }} values - tenancy and policy values from env (aws_region -> AWS_REGION) or
// explicit overrides - and passes the text as the Responses API `instructions`. No SQL lives here
// or in the prompts: the model discovers resources and their I/O contracts through the StackQL
// discovery tools and the query library at run time.
import fs from 'node:fs';
import path from 'node:path';
import { PROMPTS_DIR } from './env.js';

const PARAM_RE = /\{\{\s*([A-Za-z0-9_]+)\s*\}\}/g;

export const SERVER_INSTRUCTIONS_URI = 'stackql://docs/instructions';

export function promptFile(name, dir = PROMPTS_DIR) {
  const file = path.join(dir, `${name}.md`);
  if (!fs.existsSync(file)) throw new Error(`prompt ${name}: file not found (${file})`);
  return file;
}

export function placeholdersOf(text) {
  return [...new Set([...text.matchAll(PARAM_RE)].map((m) => m[1]))];
}

// Overrides first, then the upper-cased env var. A missing value fails fast naming the variable.
export function renderPrompt(text, overrides = {}, source = process.env, { name = 'prompt' } = {}) {
  return text.replace(PARAM_RE, (_, key) => {
    const fromOverride = overrides[key];
    const v = fromOverride !== undefined && fromOverride !== null ? String(fromOverride) : source[key.toUpperCase()];
    if (v === undefined || v === '') {
      throw new Error(`prompt ${name}: missing value for {{ ${key} }} (set ${key.toUpperCase()} in .env or pass an override)`);
    }
    return v;
  });
}

export function loadPrompt(name, overrides = {}, source = process.env, dir = PROMPTS_DIR) {
  const text = fs.readFileSync(promptFile(name, dir), 'utf8');
  return renderPrompt(text, overrides, source, { name });
}

// The discovery briefing is shared by every model-facing prompt; the server's own instructions
// (MCP resource stackql://docs/instructions) are appended when the read succeeds.
export function composeInstructions({ role, discovery, serverInstructions = '' }) {
  const parts = [role.trim(), discovery.trim()];
  if (serverInstructions && serverInstructions.trim()) parts.push(serverInstructions.trim());
  return parts.join('\n\n');
}

export async function readServerInstructions(server, { log = console.log } = {}) {
  try {
    const text = await server.readResource(SERVER_INSTRUCTIONS_URI);
    if (!text) throw new Error('empty resource');
    return text;
  } catch (e) {
    log(`  note: could not read ${SERVER_INSTRUCTIONS_URI} (${e.message}); continuing without the server instructions`);
    return '';
  }
}
