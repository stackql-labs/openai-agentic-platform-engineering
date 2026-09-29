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
export const PROMPTS_DIR = path.join(PKG_DIR, 'prompts');
export const QUERIES_DIR = path.join(PKG_DIR, 'queries');
export const EXAMPLES_DIR = path.join(QUERIES_DIR, 'examples');
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

export const NOT_CONFIGURED = 'not configured - skip this provider';

/** The IdP's display name, tenancy identifier and Okta subdomain (empty for Entra ID). */
export function idpIdentity(s: Settings): { name: string; tenant: string; oktaSubdomain: string } {
  if (s.idpProvider === 'okta') {
    const domain = env('OKTA_DOMAIN');
    return {
      name: 'Okta',
      tenant: providerConfigured('okta') ? `Okta org ${domain}` : NOT_CONFIGURED,
      oktaSubdomain: domain.split('.')[0] ?? '',
    };
  }
  return {
    name: 'Microsoft Entra ID',
    tenant: providerConfigured('entra_id') ? `Entra ID tenant ${env('AZURE_TENANT_ID')}` : NOT_CONFIGURED,
    oktaSubdomain: '',
  };
}

/** The tenancy the sweep is pinned to, one line per provider, for prompts and the report. */
export function tenancyLines(s: Settings): string[] {
  const idp = idpIdentity(s);
  const id = (provider: string, label: string, value: string) =>
    providerConfigured(provider) ? `${label} ${value}` : `${label}: ${NOT_CONFIGURED}`;
  return [
    id('aws', 'AWS account', `${env('AWS_ACCOUNT_ID')} (IAM is global; the provider signs against ${env('AWS_IAM_REGION', 'us-east-1')})`),
    id('azure', 'Azure subscription', env('AZURE_SUBSCRIPTION_ID')),
    id('google', 'Google project', env('GOOGLE_PROJECT')),
    id('github', 'GitHub org', env('GITHUB_ORG')),
    `IdP (${s.idpProvider}): ${idp.tenant}`,
    `Demo tag ${s.demoTagKey}=${s.demoTagValue}; demo name prefix ${s.demoPrefix}; privileged IdP group ${privilegedGroup(s)}`,
  ];
}

export function privilegedGroup(s: Settings): string {
  return env('IDP_PRIVILEGED_GROUP', `${s.demoPrefix}-cloud-admins`);
}

/**
 * Values for the `{{ placeholder }}`s in prompts/*.md and queries/examples/*.sql. Tenancy ids
 * of a provider without credentials render as "not configured" so the prompt still loads and
 * tells the model to skip it; a placeholder with no entry here fails in the prompt loader.
 */
export function promptValues(s: Settings): Record<string, string> {
  const idp = idpIdentity(s);
  const tenancy = (provider: string, v: string) => (providerConfigured(provider) ? v : NOT_CONFIGURED);
  return {
    idp_provider: s.idpProvider,
    idp_name: idp.name,
    idp_tenant: idp.tenant,
    okta_subdomain: idp.oktaSubdomain,
    aws_account_id: tenancy('aws', env('AWS_ACCOUNT_ID')),
    aws_iam_region: env('AWS_IAM_REGION', 'us-east-1'),
    azure_subscription_id: tenancy('azure', env('AZURE_SUBSCRIPTION_ID')),
    google_project: tenancy('google', env('GOOGLE_PROJECT')),
    github_org: tenancy('github', env('GITHUB_ORG')),
    demo_prefix: s.demoPrefix,
    demo_tag_key: s.demoTagKey,
    demo_tag_value: s.demoTagValue,
    idp_privileged_group: privilegedGroup(s),
    escalation_severity: s.escalationSeverity,
    providers_in_scope: tenancyLines(s)
      .map((l) => `- ${l}`)
      .join('\n'),
  };
}
