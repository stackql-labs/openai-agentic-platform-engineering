// Environment: the repo-root .env is shared by every use case. This module loads it once and
// exposes typed accessors. Nothing here hardcodes a model id, account id or subscription id.
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import dotenv from 'dotenv';

export const DRIFT_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const REPO_ROOT = path.resolve(DRIFT_DIR, '..');
export const QUERIES_DIR = path.join(DRIFT_DIR, 'queries');
export const STACK_DIR = path.join(DRIFT_DIR, 'stack');
export const ENV_FILE = path.join(REPO_ROOT, '.env');

dotenv.config({ path: ENV_FILE, quiet: true });

// Variables this use case reads, with their defaults. Documented in drift/README.md and
// reported to the coordinator for .env.example.
export const ENV_DEFAULTS = Object.freeze({
  SWEEP_REASONING_EFFORT: 'low',
  DEMO_PREFIX: 'agentic-demo',
  DEMO_TAG_KEY: 'purpose',
  DEMO_TAG_VALUE: 'agentic-demo',
  STACKQL_MCP_AUDIT_LOG: 'runs/stackql-mcp-audit.jsonl',
  DRIFT_SNAPSHOT_DB: 'snapshots/drift.db',
  DRIFT_MAX_TOOL_CALLS: '6',
  DRIFT_ROW_LIMIT: '5000',
});

export function env(name, fallback = ENV_DEFAULTS[name] ?? '') {
  const v = process.env[name];
  return v === undefined || v === '' ? fallback : v;
}

export function requireEnv(name) {
  const v = env(name);
  if (!v) {
    throw new Error(`missing environment variable ${name} (set it in ${ENV_FILE})`);
  }
  return v;
}

export function resolveRepoPath(p) {
  return path.isAbsolute(p) ? p : path.join(REPO_ROOT, p);
}

export function approot() {
  const v = env('STACKQL_APPROOT');
  if (!v) return path.join(os.homedir(), '.stackql');
  if (v.startsWith('~/')) return path.join(os.homedir(), v.slice(2));
  return resolveRepoPath(v);
}

export function settings() {
  return {
    sweepModel: env('SWEEP_MODEL'),
    sweepEffort: env('SWEEP_REASONING_EFFORT'),
    demoPrefix: env('DEMO_PREFIX'),
    demoTagKey: env('DEMO_TAG_KEY'),
    demoTagValue: env('DEMO_TAG_VALUE'),
    approot: approot(),
    auditLog: resolveRepoPath(env('STACKQL_MCP_AUDIT_LOG')),
    snapshotDb: resolveRepoPath(env('DRIFT_SNAPSHOT_DB')),
    runsDir: path.join(REPO_ROOT, 'runs'),
    maxToolCalls: Number.parseInt(env('DRIFT_MAX_TOOL_CALLS'), 10),
    rowLimit: Number.parseInt(env('DRIFT_ROW_LIMIT'), 10),
    awsRegion: env('AWS_REGION'),
    azureSubscriptionId: env('AZURE_SUBSCRIPTION_ID'),
  };
}

// Credentials are read by the StackQL server from its process environment (the full env is
// passed through). Code only checks presence to decide which snapshot sources to run.
const PROVIDER_ENV = Object.freeze({
  aws: ['AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_REGION'],
  azure: ['AZURE_TENANT_ID', 'AZURE_CLIENT_ID', 'AZURE_CLIENT_SECRET', 'AZURE_SUBSCRIPTION_ID'],
});

export function providerConfigured(provider, source = process.env) {
  const names = PROVIDER_ENV[provider];
  if (!names) return false;
  return names.every((n) => source[n] !== undefined && source[n] !== '');
}

export function missingProviderEnv(provider, source = process.env) {
  return (PROVIDER_ENV[provider] || []).filter((n) => !source[n]);
}
