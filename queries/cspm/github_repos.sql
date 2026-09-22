-- id: cspm/github_repos
-- scenario: cspm
-- providers: github
-- params: github_org
-- expected_columns: name, default_branch, visibility, archived, pushed_at, topics
-- description: active repositories in the org - the fan-out list for per-repo checks
SELECT name, default_branch, visibility, archived, pushed_at, topics
FROM github.repos.repos
WHERE org = '{{ github_org }}'
  AND archived IN (0, '0', 'false')
ORDER BY name
