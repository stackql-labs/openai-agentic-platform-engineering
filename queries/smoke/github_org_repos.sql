-- id: smoke/github_org_repos
-- scenario: smoke
-- providers: github
-- params: github_org
-- expected_columns: name, visibility
-- description: repositories in the demo org - proves github auth
SELECT name, visibility
FROM github.repos.repos
WHERE org = '{{ github_org }}'
