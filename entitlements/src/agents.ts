/**
 * Agent definitions for the two model tiers.
 *
 *   sweep     (SWEEP_MODEL)     runs the query pack through the read-only StackQL MCP server and
 *                               classifies rows into the shared findings shape
 *   reasoning (REASONING_MODEL) receives findings at or above ESCALATION_SEVERITY and says who
 *                               should confirm each, the least-privilege alternative, and the
 *                               StackQL statement that would remove the grant (never executed)
 *
 * Model ids and reasoning effort come from the environment; nothing is hardcoded.
 */

import { Agent } from '@openai/agents';
import type { MCPServer, ModelSettings, Tool } from '@openai/agents';
import type { ZodType } from 'zod';
import { providerConfigured, tenancyLines, type ModelTier, type Settings } from './config.ts';
import { listQueries, paramValues, renderQuery, type Query } from './queries.ts';
import { env } from './config.ts';

export const DIALECT_NOTES = `StackQL dialect notes (SQLite backend):
- Providers are queried as provider.service.resource. Use the canonical queries you are given;
  only call list_resources / describe_resource when a query is missing.
- AWS resources need region = '...' in WHERE (IAM signs against us-east-1). Azure resources need
  subscription_id. Google resources need project or projectsId. GitHub resources need org or
  owner/repo. Okta resources need subdomain. Entra ID resources need no tenancy predicate (the
  credentials pin the tenant).
- JSON columns are unpacked with JSON_EXTRACT(col, '$.path') and json_each(col) inside a CTE.
- Booleans compare as 0/1 or 'true'/'false' depending on provider.
- An empty result is zero rows, not an error. Do not retry a query that returned zero rows.
- One CTE level at most; fan-out parameters (IN lists) must be literals rendered by render_query.
- A 403 or "insufficient privileges" from the IdP means the app registration or API token lacks
  read consent: report it as one info finding (idp_access_blocked), do not retry.`;

/** Parameters the model supplies at run time (fan-out from inventory rows). */
export const DYNAMIC_HINTS: Record<string, string> = {
  iam_user_name: 'JSON array of IAM user names from entitlements/aws_iam_users',
  aws_admin_users:
    'JSON array of the IAM user names whose attached policies include AdministratorAccess (empty array if none)',
  github_repo_list: 'JSON array of repo names from entitlements/github_repos that start with the demo prefix',
  idp_group_id: 'the id returned by the idp_groups query',
};

export interface PackEntry {
  query: Query;
  status: 'static' | 'dynamic' | 'skipped';
  dynamicParams: string[];
  sql?: string;
  note?: string;
}

function unresolvable(q: Query): string[] {
  const values = paramValues();
  return q.params.filter((p) => (values[p] ?? '') === '' && env(p.toUpperCase()) === '');
}

/** The ordered query pack for the selected IdP, with provider credential gating. */
export function buildPack(s: Settings): PackEntry[] {
  const all = new Map(listQueries().map((q) => [q.id, q]));
  const clouds = ['aws', 'azure', 'google'];
  const allClouds = clouds.every(providerConfigured);
  const idpPrefix = s.idpProvider === 'okta' ? 'entitlements/okta_' : 'entitlements/';
  const order = [
    'entitlements/aws_iam_users',
    'entitlements/aws_iam_user_attached_policies',
    ...(allClouds
      ? ['entitlements/privileged_principals_all_clouds']
      : ['entitlements/azure_owner_assignments_subscription_scope', 'entitlements/google_project_owner_bindings']),
    'entitlements/github_repos',
    'entitlements/github_outside_collaborators_admin',
    `${idpPrefix}github_members_vs_idp`,
    `${idpPrefix}idp_groups`,
    `${idpPrefix}idp_privileged_group_members`,
  ];
  const out: PackEntry[] = [];
  for (const id of order) {
    const q = all.get(id);
    if (!q) throw new Error(`query pack references missing query ${id}`);
    const missingCreds = q.providers.filter((p) => !providerConfigured(p));
    if (missingCreds.length) {
      out.push({ query: q, status: 'skipped', dynamicParams: [], note: `credentials for ${missingCreds.join(', ')} are not configured` });
      continue;
    }
    const dyn = unresolvable(q).map((p) => (p === 'aws_admins_sql' ? 'aws_admin_users' : p));
    if (dyn.length) {
      out.push({ query: q, status: 'dynamic', dynamicParams: dyn });
    } else {
      out.push({ query: q, status: 'static', dynamicParams: [], sql: renderQuery(q) });
    }
  }
  return out;
}

export function packText(pack: PackEntry[]): string {
  const parts: string[] = [];
  for (const e of pack) {
    const q = e.query;
    if (e.status === 'skipped') {
      parts.push(`### ${q.id}\n(skipped: ${e.note})\n`);
    } else if (e.status === 'dynamic') {
      const hints = e.dynamicParams.map((p) => `${p}: ${DYNAMIC_HINTS[p] ?? 'value from an earlier result'}`);
      parts.push(
        `### ${q.id}\n${q.description}\nDynamic parameters - call render_query with a JSON object for: \n- ${hints.join('\n- ')}\nthen run the returned SQL verbatim.\n`,
      );
    } else {
      parts.push(`### ${q.id}\n${q.description}\n\`\`\`sql\n${e.sql}\n\`\`\`\n`);
    }
  }
  return parts.join('\n');
}

export function sweepInstructions(s: Settings): string {
  return `You are a scheduled, read-only entitlements recertification sweep over a demo estate. You run
on a timer; nobody typed a prompt. Work through the query pack in the order given:

1. For each query, obtain the SQL (given inline, or via render_query for fan-out parameters -
   take the parameter values from the results of earlier queries) and run it with
   run_select_query exactly as rendered. Do not rewrite SQL. Do not call any other tool. Skip a
   fan-out query when its input list is empty.
2. Turn rows into findings using the shared schema, one finding per principal and grant. Rows
   whose status column says 'ok' or 'active' are not findings. Zero rows means no finding for
   that query - move on, do not retry.
3. evidence is the query id plus the identifying values from the row (principal, role or policy,
   scope, account status). proposed_remediation is the StackQL statement that would remove the
   grant (DELETE the role assignment, EXEC detach_user_policy, DELETE the collaborator, DELETE the
   group member) - it is never executed by you. Set monthly_cost_estimate_usd to null.
4. Severity:
   - critical: a disabled (leaver) identity that still holds privilege - status
     'leaver still privileged' or 'leaver: IdP account disabled' (finding_type leaver_privileged)
   - high: a service account or other non-human principal holding roles/owner, Owner or
     AdministratorAccess (nonhuman_privileged); an outside collaborator with admin on a repo
     (outside_collaborator_admin)
   - medium: a GitHub org member with no IdP identity (orphan_member); a human holding Owner at
     subscription scope, roles/owner on the project, or AdministratorAccess in the account
     (human_owner_subscription for Azure, privileged_principal otherwise)
   - info: the IdP returned 403 or an authorization error (idp_access_blocked, provider
     ${s.idpProvider}); then do not report every GitHub member as an orphan - say the IdP was
     unreachable instead
5. AWS: run entitlements/aws_iam_users, then entitlements/aws_iam_user_attached_policies for
   every user (iam_user_name as a JSON array), collect the users whose policy_arn ends with
   /AdministratorAccess and pass them as aws_admin_users when rendering
   entitlements/privileged_principals_all_clouds (pass an empty array if none).
6. GitHub: run the collaborator query only for repos whose name starts with '${s.demoPrefix}'.
7. Finish with a summary of two to four sentences, matter of fact.

Tenancy in scope (the only accounts you may query):
${tenancyLines(s)
  .map((l) => `- ${l}`)
  .join('\n')}

${DIALECT_NOTES}`;
}

export function reasoningInstructions(s: Settings): string {
  return `You are the reasoning tier for an entitlements recertification. You receive findings a
smaller model classified at or above the escalation threshold. For each one:
- decide whether it is material in this recertification cycle, correlating with the other
  findings where that changes the answer (two or three sentences of rationale);
- name who should confirm or revoke it (a role or team such as the cloud platform owner, the
  repository owner, the identity team - not a person's name);
- give the least-privilege alternative that would meet the same need;
- draft the single StackQL mutation statement that would remove the grant, pinned to the tenancy
  below, for human review only. Forms: DELETE FROM azure.authorization.role_assignments WHERE ...;
  EXEC aws.iam.user_policies.detach_user_policy @UserName=..., @PolicyArn=..., @region='us-east-1';
  DELETE FROM github.repos.collaborators WHERE owner=... AND repo=... AND username=...;
  DELETE FROM entra_id.groups.members WHERE group_id=... AND directory_object_id=...;
  DELETE FROM okta.groups.users WHERE groupId=... AND userId=... AND subdomain=...;
  for a GCP binding, an UPDATE of google.cloudresourcemanager.projects_iam_policies with the
  member removed. This program never executes them.
- state the blast radius.
You may run at most three read-only SELECTs through StackQL to confirm a detail that changes a
verdict (for example describe_resource to check a column name); do not repeat the sweep. Never
propose anything outside the tenancy below. Return one assessment per finding fingerprint you were
given, plus a one-paragraph executive summary for the recertification report.

Tenancy:
${tenancyLines(s)
  .map((l) => `- ${l}`)
  .join('\n')}

${DIALECT_NOTES}`;
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
  opts: { name: string; instructions: string; mcpServers: MCPServer[]; tools?: Tool[]; outputType: T },
): Agent<unknown, T> {
  return new Agent({
    name: opts.name,
    instructions: opts.instructions,
    model: t.model,
    modelSettings: modelSettingsFor(t),
    mcpServers: opts.mcpServers,
    tools: opts.tools ?? [],
    outputType: opts.outputType,
  });
}
