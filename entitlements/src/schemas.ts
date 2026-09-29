/**
 * Structured outputs. Every sweep in this repo emits the same findings shape so downstream
 * artifacts (recertification report, run record, brief) and the escalation step are generic.
 * zod 4 schemas double as the JSON schema the model is constrained to.
 */

import { createHash } from 'node:crypto';
import { z } from 'zod';

export const SEVERITY_ORDER = ['info', 'low', 'medium', 'high', 'critical'] as const;
export type Severity = (typeof SEVERITY_ORDER)[number];
export const SeveritySchema = z.enum(SEVERITY_ORDER);

export function severityAtLeast(sev: string, threshold: string): boolean {
  const a = SEVERITY_ORDER.indexOf(sev as Severity);
  const b = SEVERITY_ORDER.indexOf(threshold as Severity);
  if (a < 0 || b < 0) throw new Error(`unknown severity '${a < 0 ? sev : threshold}'`);
  return a >= b;
}

export const FindingSchema = z.object({
  provider: z.string().describe('aws | azure | google | github | entra_id | okta'),
  resource: z.string().describe('Stable identifier of the principal or grant (ARN, object id, login, repo)'),
  finding_type: z
    .string()
    .describe(
      'snake_case check id: leaver_privileged | nonhuman_privileged | outside_collaborator_admin | orphan_member | human_owner_subscription | privileged_principal | idp_access_blocked',
    ),
  severity: SeveritySchema,
  title: z.string().describe('One line, matter of fact'),
  evidence: z.string().describe('Query id and the supporting row values, as text'),
  proposed_remediation: z
    .string()
    .describe('The StackQL statement or configuration change that would remove the grant - never executed by this program'),
  query_id: z.string().describe('entitlements/<name> the evidence came from'),
  monthly_cost_estimate_usd: z.number().nullable().describe('Not used by this use case; always null'),
});
export type Finding = z.infer<typeof FindingSchema>;

export const FindingSetSchema = z.object({
  scenario: z.string(),
  findings: z.array(FindingSchema),
  summary: z.string().describe('Two to four sentences for the console brief'),
});
export type FindingSet = z.infer<typeof FindingSetSchema>;

export const AssessmentSchema = z.object({
  finding_fingerprint: z.string(),
  material: z.boolean().describe('True if this warrants action in this recertification cycle'),
  confirmer: z.string().describe('Who should confirm or revoke this entitlement (role or team, not a person name)'),
  rationale: z.string().describe('Two or three sentences, correlating with other findings where relevant'),
  least_privilege_alternative: z.string().describe('The narrower grant that would meet the same need'),
  removal_statement: z
    .string()
    .describe('The StackQL mutation statement that would remove the grant, for human review only - never executed'),
  blast_radius: z.string().describe('What else the removal touches'),
  correlated_fingerprints: z.array(z.string()),
});
export type Assessment = z.infer<typeof AssessmentSchema>;

export const AssessmentSetSchema = z.object({
  assessments: z.array(AssessmentSchema),
  executive_summary: z.string(),
});
export type AssessmentSet = z.infer<typeof AssessmentSetSchema>;

export function fingerprint(f: Pick<Finding, 'provider' | 'finding_type' | 'resource'>): string {
  const raw = `${f.provider}|${f.finding_type}|${f.resource}`.toLowerCase();
  return createHash('sha1').update(raw).digest('hex').slice(0, 10);
}

export function escalationCandidates(fs: FindingSet, threshold: string): Finding[] {
  return fs.findings.filter((f) => severityAtLeast(f.severity, threshold));
}

export function sortBySeverity<T extends { severity: string }>(items: T[]): T[] {
  return [...items].sort(
    (a, b) => SEVERITY_ORDER.indexOf(b.severity as Severity) - SEVERITY_ORDER.indexOf(a.severity as Severity),
  );
}
