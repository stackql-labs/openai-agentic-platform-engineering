import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { listQueriesForModel, renderQueryForModel, renderQueryTool } from '../src/tools.ts';

describe('render_query tool', () => {
  process.env.GITHUB_ORG = 'demo-org';
  process.env.AWS_ACCOUNT_ID = '000000000000';
  process.env.AZURE_SUBSCRIPTION_ID = '00000000-0000-0000-0000-000000000000';
  process.env.GOOGLE_PROJECT = 'demo-project';
  process.env.DEMO_PREFIX = 'agentic-demo';

  it('renders a static query with "{}"', () => {
    const sql = renderQueryForModel('entitlements/github_repos', '{}');
    assert.match(sql, /FROM github\.repos\.repos/);
    assert.match(sql, /org = 'demo-org'/);
  });

  it('list parameters become literal IN lists', () => {
    const sql = renderQueryForModel(
      'entitlements/github_outside_collaborators_admin',
      JSON.stringify({ github_repo_list: ['agentic-demo-a', "agentic-demo-b'c"] }),
    );
    assert.match(sql, /repo IN \('agentic-demo-a', 'agentic-demo-b''c'\)/);
    assert.throws(() => renderQueryForModel('entitlements/github_outside_collaborators_admin', '{"github_repo_list": []}'), /empty list/);
  });

  it('per-key parameters fan out into UNION ALL', () => {
    const sql = renderQueryForModel('entitlements/aws_iam_user_attached_policies', '{"iam_user_name": ["alice", "bob"]}');
    assert.equal(sql.split('UNION ALL').length, 2);
    assert.match(sql, /'bob' AS user_name/);
  });

  it('aws_admin_users renders the aws_admins CTE body of the all-clouds query', () => {
    const sql = renderQueryForModel('entitlements/privileged_principals_all_clouds', '{"aws_admin_users": ["agentic-demo-admin"]}');
    assert.match(sql, /aws_admins AS \(\s*SELECT 'agentic-demo-admin' AS user_name\s*\)/);
    assert.match(sql, /account 000000000000/);
    const none = renderQueryForModel('entitlements/privileged_principals_all_clouds', '{"aws_admin_users": []}');
    assert.match(none, /WHERE 1 = 0/);
  });

  it('rejects unknown ids and non-object params', () => {
    assert.throws(() => renderQueryForModel('entitlements/nope', '{}'), /no query with id/);
    assert.throws(() => renderQueryForModel('entitlements/github_repos', '[1]'), /JSON object/);
  });

  it('is exposed to the model as a non-mutating function tool', () => {
    assert.equal(renderQueryTool.type, 'function');
    assert.equal(renderQueryTool.name, 'render_query');
  });

  it('list_queries only shows the selected IdP variant', () => {
    const entra = listQueriesForModel('entra_id');
    assert.match(entra, /entitlements\/idp_groups/);
    assert.doesNotMatch(entra, /okta_idp_groups/);
    const okta = listQueriesForModel('okta');
    assert.match(okta, /okta_idp_groups/);
    assert.doesNotMatch(okta, /entitlements\/idp_groups:/);
  });
});
