import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  AssessmentSetSchema,
  FindingSchema,
  FindingSetSchema,
  SEVERITY_ORDER,
  escalationCandidates,
  fingerprint,
  severityAtLeast,
  sortBySeverity,
} from '../src/schemas.ts';

const finding = {
  provider: 'entra_id',
  resource: 'agentic-demo-leaver@example.onmicrosoft.com',
  finding_type: 'leaver_privileged',
  severity: 'critical',
  title: 'Disabled account still in agentic-demo-cloud-admins',
  evidence: 'entitlements/idp_privileged_group_members: status=leaver still privileged',
  proposed_remediation: "DELETE FROM entra_id.groups.members WHERE group_id = 'g' AND directory_object_id = 'u'",
  query_id: 'entitlements/idp_privileged_group_members',
  monthly_cost_estimate_usd: null,
};

describe('findings schema', () => {
  it('accepts the shared shape', () => {
    const f = FindingSchema.parse(finding);
    assert.equal(f.severity, 'critical');
    assert.equal(f.monthly_cost_estimate_usd, null);
  });

  it('rejects an unknown severity and missing fields', () => {
    assert.throws(() => FindingSchema.parse({ ...finding, severity: 'urgent' }));
    const { evidence: _e, ...rest } = finding;
    assert.throws(() => FindingSchema.parse(rest));
  });

  it('severity order and threshold comparison', () => {
    assert.deepEqual([...SEVERITY_ORDER], ['info', 'low', 'medium', 'high', 'critical']);
    assert.ok(severityAtLeast('critical', 'high'));
    assert.ok(severityAtLeast('high', 'high'));
    assert.ok(!severityAtLeast('medium', 'high'));
    assert.throws(() => severityAtLeast('bogus', 'high'));
  });

  it('fingerprint is stable and case-insensitive', () => {
    const a = fingerprint(finding);
    const b = fingerprint({ provider: finding.provider, finding_type: finding.finding_type, resource: finding.resource.toUpperCase() });
    assert.equal(a, b);
    assert.match(a, /^[0-9a-f]{10}$/);
    assert.notEqual(a, fingerprint({ ...finding, finding_type: 'orphan_member' }));
  });

  it('escalation candidates and severity sort', () => {
    const fs = FindingSetSchema.parse({
      scenario: 'entitlements',
      summary: 's',
      findings: [
        { ...finding, severity: 'medium', resource: 'a' },
        { ...finding, severity: 'critical', resource: 'b' },
        { ...finding, severity: 'high', resource: 'c' },
      ],
    });
    assert.deepEqual(
      escalationCandidates(fs, 'high').map((f) => f.resource),
      ['b', 'c'],
    );
    assert.deepEqual(
      sortBySeverity(fs.findings).map((f) => f.severity),
      ['critical', 'high', 'medium'],
    );
  });

  it('assessment set requires the recertification fields', () => {
    const ok = AssessmentSetSchema.parse({
      executive_summary: 'x',
      assessments: [
        {
          finding_fingerprint: 'abc',
          material: true,
          confirmer: 'identity team',
          rationale: 'r',
          least_privilege_alternative: 'none - remove',
          removal_statement: 'DELETE FROM ...',
          blast_radius: 'one group membership',
          correlated_fingerprints: [],
        },
      ],
    });
    assert.equal(ok.assessments[0]!.confirmer, 'identity team');
    assert.throws(() => AssessmentSetSchema.parse({ executive_summary: 'x', assessments: [{ finding_fingerprint: 'abc' }] }));
  });
});
