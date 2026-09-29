import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, it } from 'node:test';
import { QUERIES_DIR } from '../src/config.ts';
import { listQueries, parseQuery, placeholdersIn, renderQuery, renderUnion, sqlList } from '../src/queries.ts';
import { projectedColumns, resourceOf, sourceColumns, splitCtes, unionColumns } from '../src/sqlparts.ts';

describe('query loader', () => {
  const queries = listQueries();

  it('loads every committed query with a complete header', () => {
    assert.ok(queries.length >= 14);
    for (const q of queries) {
      assert.match(q.id, /^entitlements\/[a-z0-9_]+$/, q.path);
      assert.ok(q.providers.length > 0, `${q.id} has no providers`);
      assert.ok(q.description.length > 0, `${q.id} has no description`);
      assert.ok(q.expectedColumns.length > 0, `${q.id} has no expected_columns`);
      assert.ok(/^SELECT|^WITH/i.test(q.sql), `${q.id} is not a SELECT`);
      assert.ok(!q.sql.trim().endsWith(';'));
    }
  });

  it('declares exactly the placeholders it uses', () => {
    for (const q of queries) {
      const used = placeholdersIn(q.sql).sort();
      assert.deepEqual([...q.params].sort(), used, `${q.id}: params header vs placeholders`);
    }
  });

  it('ids are unique and match the file name', () => {
    const ids = new Set(queries.map((q) => q.id));
    assert.equal(ids.size, queries.length);
    for (const q of queries) assert.ok(q.path.endsWith(`${q.id.replace('entitlements/', '')}.sql`));
  });

  it('every IdP query is tagged with its idp and there is an okta twin for each entra_id query', () => {
    const entra = queries.filter((q) => q.idp === 'entra_id').map((q) => q.id.replace('entitlements/', ''));
    const okta = queries.filter((q) => q.idp === 'okta').map((q) => q.id.replace('entitlements/okta_', ''));
    assert.deepEqual(entra.sort(), okta.sort());
    for (const q of queries) {
      if (q.providers.includes('entra_id')) assert.equal(q.idp, 'entra_id', q.id);
      if (q.providers.includes('okta')) assert.equal(q.idp, 'okta', q.id);
    }
  });

  it('parses a header and strips a trailing semicolon', () => {
    const q = parseQuery(
      '-- id: entitlements/x\n-- providers: aws\n-- params: a\n-- expected_columns: c\n-- description: d\nSELECT {{ a }} AS c;\n',
      `${QUERIES_DIR}/x.sql`,
    );
    assert.equal(q.id, 'entitlements/x');
    assert.deepEqual(q.params, ['a']);
    assert.equal(q.sql, 'SELECT {{ a }} AS c');
    assert.equal(q.idp, null);
  });
});

describe('render', () => {
  it('substitutes from the environment, upper-cased param name', () => {
    process.env.GITHUB_ORG = 'demo-org';
    const q = listQueries().find((x) => x.id === 'entitlements/github_repos')!;
    const sql = renderQuery(q);
    assert.match(sql, /org = 'demo-org'/);
    assert.ok(!sql.includes('{{'));
  });

  it('fails fast naming the variable when a value is missing', () => {
    delete process.env.AZURE_SUBSCRIPTION_ID;
    const q = listQueries().find((x) => x.id === 'entitlements/azure_owner_assignments_subscription_scope')!;
    assert.throws(() => renderQuery(q), /azure_subscription_id.*AZURE_SUBSCRIPTION_ID/);
  });

  it('explicit overrides win over the environment', () => {
    process.env.GITHUB_ORG = 'demo-org';
    const q = listQueries().find((x) => x.id === 'entitlements/github_repos')!;
    assert.match(renderQuery(q, { github_org: 'other' }), /org = 'other'/);
  });

  it('derived defaults: aws_iam_region, demo_prefix, idp_privileged_group', () => {
    delete process.env.AWS_IAM_REGION;
    delete process.env.IDP_PRIVILEGED_GROUP;
    process.env.DEMO_PREFIX = 'agentic-demo';
    const users = listQueries().find((x) => x.id === 'entitlements/aws_iam_users')!;
    assert.match(renderQuery(users), /region = 'us-east-1'/);
    const groups = listQueries().find((x) => x.id === 'entitlements/idp_groups')!;
    assert.match(renderQuery(groups), /display_name = 'agentic-demo-cloud-admins'/);
  });

  it('sqlList renders escaped literals', () => {
    assert.equal(sqlList(['a', "b'c"]), "'a', 'b''c'");
  });

  it('renderUnion renders one SELECT per value joined with UNION ALL', () => {
    const q = listQueries().find((x) => x.id === 'entitlements/aws_iam_user_attached_policies')!;
    const sql = renderUnion(q, 'iam_user_name', ['alice', 'bob']);
    assert.equal(sql.split('UNION ALL').length, 2);
    assert.match(sql, /'alice' AS user_name/);
    assert.match(sql, /user_name = 'bob'/);
    assert.throws(() => renderUnion(q, 'iam_user_name', []), /no values/);
  });
});

describe('sql splitting for validation', () => {
  it('splits CTEs and the outer statement', () => {
    const { ctes, outer } = splitCtes(
      "WITH a AS (SELECT x, JSON_EXTRACT(p, '$.k)') AS k FROM t WHERE q = '(' ), b AS (SELECT y FROM u) SELECT a.x FROM a JOIN b",
    );
    assert.deepEqual(
      ctes.map((c) => c.name),
      ['a', 'b'],
    );
    assert.equal(ctes[1]!.body, 'SELECT y FROM u');
    assert.equal(outer, 'SELECT a.x FROM a JOIN b');
    assert.deepEqual(projectedColumns(ctes[0]!.body), ['x', 'k']);
  });

  it('plain SELECT has no CTEs', () => {
    assert.deepEqual(splitCtes('SELECT 1 AS one').ctes, []);
  });

  it('projected columns handle aliases, dotted names and CASE', () => {
    assert.deepEqual(projectedColumns('SELECT t.id, name AS n, CASE WHEN a = 1 THEN 2 ELSE 3 END AS c FROM t'), ['id', 'n', 'c']);
    assert.deepEqual(unionColumns("SELECT 'a' AS user_name\nUNION ALL\nSELECT 'b' AS user_name"), ['user_name']);
  });

  it('source columns and resource of a provider SELECT', () => {
    const body = "SELECT id, userPrincipalName AS upn, JSON_EXTRACT(profile, '$.login') AS l, 'x' AS lit FROM entra_id.users.users WHERE a = 1";
    assert.deepEqual(sourceColumns(body), ['id', 'userPrincipalName']);
    assert.deepEqual(resourceOf(body), { provider: 'entra_id', service: 'users', resource: 'users' });
    assert.equal(resourceOf('SELECT 1 AS x'), null);
  });

  it('every committed CTE query splits cleanly', () => {
    for (const q of listQueries()) {
      const text = readFileSync(q.path, 'utf8');
      if (!/^WITH/m.test(text)) continue;
      const { ctes, outer } = splitCtes(q.sql);
      assert.ok(ctes.length > 0, q.id);
      assert.match(outer, /^SELECT/i, q.id);
    }
  });
});
