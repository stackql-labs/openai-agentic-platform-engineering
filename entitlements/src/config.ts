/**
 * Environment-driven configuration. Nothing here is hardcoded to a model, account, org or
 * tenant: everything comes from the repo-root .env (see .env.example). Missing required values
 * fail fast with the variable name so the fix is obvious.
 */

import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { config as loadDotenv } from 'dotenv';

export const PKG_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const REPO_ROOT = path.resolve(PKG_DIR, '..');
export const ENV_FILE = path.join(REPO_ROOT, '.env');
export const QUERIES_DIR = path.join(PKG_DIR, 'queries');
export const RUNS_DIR = path.join(REPO_ROOT, 'runs');
export const PRICING_FILE = path.join(REPO_ROOT, 'pricing.json');

// Shared .env at the repo root; values already present in the process environment win.
if (existsSync(ENV_FILE)) {
  loadDotenv({ path: ENV_FILE, override: false, quiet: true });
}

export class ConfigError extends Error {}

export function env(name: string, fallback?: string): string {
  const raw = process.env[name] ?? '';
  if (raw === '' && fallback !== undefined) return fallback;
  return raw;
}

export function requireEnv(name: string): string {
  const v = env(name);
  if (v === '') {
    throw new ConfigError(`${name} is not set - add it to ${ENV_FILE} (see .env.example)`);
  }
  return v;
}

export type IdpProvider = 'entra_id' | 'okta';
export const IDP_PROVIDERS: readonly IdpProvider[] = ['entra_id', 'okta'];

export interface ModelTier {
  name: 'sweep' | 'reasoning';
  model: string;
  reasoningEffort: string;
}

export interface Settings {
  sweep: ModelTier;
  reasoning: ModelTier;
  idpProvider: IdpProvider;
  stackqlApproot: string;
  mcpAuditLog: string;
  demoPrefix: string;
  demoTagKey: string;
  demoTagValue: string;
  escalationSeverity: string;
  /** Every provider the sweep can touch: the four clouds/SaaS plus the selected IdP. */
  providers: string[];
}

function resolveFromRoot(p: string): string {
  return path.isAbsolute(p) ? p : path.join(REPO_ROOT, p);
}

function expandHome(p: string): string {
  if (p === '~' || p.startsWith('~/')) {
    return path.join(process.env.HOME ?? process.env.USERPROFILE ?? '', p.slice(1));
  }
  return p;
}

export function settings(overrides: { idp?: string } = {}): Settings {
  const idpRaw = overrides.idp ?? env('IDP_PROVIDER', 'entra_id');
  if (!IDP_PROVIDERS.includes(idpRaw as IdpProvider)) {
    throw new ConfigError(`IDP_PROVIDER must be one of ${IDP_PROVIDERS.join('|')}, got '${idpRaw}'`);
  }
  const idpProvider = idpRaw as IdpProvider;
  const demoPrefix = env('DEMO_PREFIX', 'agentic-demo');
  return {
    sweep: {
      name: 'sweep',
      model: env('SWEEP_MODEL'),
      reasoningEffort: env('SWEEP_REASONING_EFFORT', 'low'),
    },
    reasoning: {
      name: 'reasoning',
      model: env('REASONING_MODEL'),
      reasoningEffort: env('REASONING_REASONING_EFFORT', 'medium'),
    },
    idpProvider,
    stackqlApproot: expandHome(env('STACKQL_APPROOT', '~/.stackql')),
    mcpAuditLog: resolveFromRoot(env('STACKQL_MCP_AUDIT_LOG', 'runs/stackql-mcp-audit.jsonl')),
    demoPrefix,
    demoTagKey: env('DEMO_TAG_KEY', 'purpose'),
    demoTagValue: env('DEMO_TAG_VALUE', 'agentic-demo'),
    escalationSeverity: env('ESCALATION_SEVERITY', 'high'),
    providers: ['aws', 'azure', 'google', 'github', idpProvider],
  };
}

/**
 * Query parameters that are derived rather than read one-to-one from the environment.
 * The loader falls back to these when no <PARAM_UPPERCASE> variable is set.
 */
export function derivedParams(): Record<string, string> {
  const demoPrefix = env('DEMO_PREFIX', 'agentic-demo');
  const oktaDomain = env('OKTA_DOMAIN');
  return {
    demo_prefix: demoPrefix,
    aws_iam_region: 'us-east-1',
    idp_privileged_group: `${demoPrefix}-cloud-admins`,
    okta_subdomain: oktaDomain === '' ? '' : (oktaDomain.split('.')[0] ?? ''),
  };
}

/** Credentials StackQL needs per provider, following its environment variable conventions. */
export const PROVIDER_ENV: Record<string, string[]> = {
  aws: ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_ACCOUNT_ID'],
  azure: ['AZURE_TENANT_ID', 'AZURE_CLIENT_ID', 'AZURE_CLIENT_SECRET', 'AZURE_SUBSCRIPTION_ID'],
  google: ['GOOGLE_CREDENTIALS', 'GOOGLE_PROJECT'],
  github: ['STACKQL_GITHUB_USERNAME', 'STACKQL_GITHUB_PASSWORD', 'GITHUB_ORG'],
  entra_id: ['AZURE_TENANT_ID', 'AZURE_CLIENT_ID', 'AZURE_CLIENT_SECRET'],
  okta: ['OKTA_DOMAIN', 'OKTA_API_TOKEN'],
};

export function providerConfigured(provider: string): boolean {
  return (PROVIDER_ENV[provider] ?? []).every((v) => env(v) !== '');
}

/** The tenancy the sweep is pinned to, for prompts and the run record. */
export function tenancyLines(s: Settings): string[] {
  const lines = [
    `AWS account ${env('AWS_ACCOUNT_ID', '(unset)')} (IAM is global; the provider signs against us-east-1)`,
    `Azure subscription ${env('AZURE_SUBSCRIPTION_ID', '(unset)')}`,
    `Google project ${env('GOOGLE_PROJECT', '(unset)')}`,
    `GitHub org ${env('GITHUB_ORG', '(unset)')}`,
  ];
  if (s.idpProvider === 'okta') {
    lines.push(`IdP: Okta org ${env('OKTA_DOMAIN', '(unset)')}`);
  } else {
    lines.push(`IdP: Microsoft Entra ID tenant ${env('AZURE_TENANT_ID', '(unset)')}`);
  }
  lines.push(
    `Demo tag ${s.demoTagKey}=${s.demoTagValue}; demo name prefix ${s.demoPrefix}; privileged IdP group ${derivedParamsWithEnv().idp_privileged_group}`,
  );
  return lines;
}

/** derivedParams() with the matching environment variables applied on top. */
export function derivedParamsWithEnv(): Record<string, string> {
  const out = derivedParams();
  for (const k of Object.keys(out)) {
    const v = env(k.toUpperCase());
    if (v !== '') out[k] = v;
  }
  return out;
}
