-- id: cspm/github_unprotected_default_branches
-- scenario: cspm
-- providers: github
-- params: github_org, github_repo_list
-- expected_columns: repo, branch, protected
-- description: main/master branches with no branch protection; fan-out over github_repo_list from cspm/github_repos
SELECT repo, name AS branch, protected
FROM github.repos.branches
WHERE owner = '{{ github_org }}'
  AND repo IN ({{ github_repo_list }})
  AND name IN ('main', 'master')
  AND protected IN (0, '0', 'false')
