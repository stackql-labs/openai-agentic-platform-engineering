-- id: cspm/github_outside_collaborators_admin
-- scenario: cspm
-- providers: github
-- params: github_org, github_repo_list
-- expected_columns: repo, login, role_name
-- description: repo collaborators with admin who are not org members (outside collaborators); LEFT JOIN of collaborators to org members
WITH collab AS (
  SELECT repo, login, role_name
  FROM github.repos.collaborators
  WHERE owner = '{{ github_org }}'
    AND repo IN ({{ github_repo_list }})
),
members AS (
  SELECT login FROM github.orgs.members WHERE org = '{{ github_org }}'
)
SELECT c.repo, c.login, c.role_name
FROM collab c
LEFT JOIN members m ON m.login = c.login
WHERE m.login IS NULL
  AND c.role_name = 'admin'
