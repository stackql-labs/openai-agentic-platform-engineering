import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { EXAMPLES_DIR, QUERIES_DIR } from '../src/config.ts';
import { listQueries, loadQuery, parseQuery, placeholdersIn, renderQuery } from '../src/queries.ts';

describe('example query loader', () => {
  const queries = listQueries();

  it('loads two or three examples, each with a complete header and a flat SELECT', () => {
    assert.ok(queries.length >= 2 && queries.length <= 3, `expected 2-3 examples, found ${queries.length}`);
    for (const q of queries) {
      assert.match(q.id, /^entitlements\/examples\/[a-z0-9_]+$/, q.path);
      assert.ok(q.path.startsWith(EXAMPLES_DIR));
      assert.ok(q.providers.length > 0, `${q.id} has no providers`);
      assert.ok(q.description.length > 0, `${q.id} has no description`);
      assert.ok(q.expectedColumns.length > 0, `${q.id} has no expected_columns`);
      assert.match(q.sql, /^SELECT/i, `${q.id} is not a flat SELECT (validate_select_query rejects WITH)`);
      assert.ok(!q.sql.trim().endsWith(';'));
    }
  });

  it('declares exactly the placeholders it uses', () => {
    for (const q of queries) {
      assert.deepEqual([...q.params].sort(), placeholdersIn(q.sql).sort(), `${q.id}: params header vs placeholders`);
    }
  });

  it('keeps the cross-cloud UNION and one IdP join as the examples', () => {
    const union = loadQuery('entitlements/examples/privileged_principals_all_clouds');
    assert.equal(union.sql.split(/\bUNION ALL\b/).length, 3);
    assert.deepEqual(union.providers, ['aws', 'azure', 'google']);
    const join = loadQuery('entitlements/examples/github_members_vs_idp');
    assert.match(join.sql, /LEFT JOIN/);
    assert.ok(join.providers.includes('github'));
    assert.throws(() => loadQuery('entitlements/aws_iam_users'), /no query with id/);
  });

  it('parses a header, derives the id from the path and strips a trailing semicolon', () => {
    const q = parseQuery('-- providers: aws\n-- params: a\n-- expected_columns: c\n-- description: d\nSELECT {{ a }} AS c;\n', `${QUERIES_DIR}/examples/x.sql`);
    assert.equal(q.id, 'entitlements/examples/x');
    assert.deepEqual(q.params, ['a']);
    assert.equal(q.sql, 'SELECT {{ a }} AS c');
  });
});

describe('render', () => {
  const join = () => loadQuery('entitlements/examples/github_members_vs_idp');

  it('explicit values win, then the upper-cased environment variable', () => {
    process.env.GITHUB_ORG = 'env-org';
    process.env.DEMO_PREFIX = 'agentic-demo';
    assert.match(renderQuery(join()), /org = 'env-org'/);
    const sql = renderQuery(join(), { github_org: 'other' });
    assert.match(sql, /org = 'other'/);
    assert.match(sql, /'agentic-demo-' \|\| gh\.login/);
    assert.ok(!sql.includes('{{'));
  });

  it('fails fast naming the variable when a value is missing', () => {
    delete process.env.GITHUB_ORG;
    assert.throws(() => renderQuery(join(), { demo_prefix: 'p' }), /github_org.*GITHUB_ORG/);
  });

  it('lenient rendering keeps an unresolved placeholder visible', () => {
    delete process.env.GITHUB_ORG;
    const sql = renderQuery(join(), { demo_prefix: 'p' }, { lenient: true });
    assert.match(sql, /org = '\{\{ github_org \}\}'/);
  });
});
