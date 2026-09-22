-- id: cspm/github_deploy_keys
-- scenario: cspm
-- providers: github
-- params: github_org, github_repo_list, deploy_key_max_age_days
-- expected_columns: repo, key_id, title, created_at, last_used, read_only, age_days
-- description: deploy keys older than the threshold (real age from created_at); fan-out over github_repo_list
WITH k AS (
  SELECT repo, id AS key_id, title, created_at, last_used, read_only,
    ROUND(julianday('now') - julianday(created_at), 0) AS age_days
  FROM github.repos.deploy_keys
  WHERE owner = '{{ github_org }}'
    AND repo IN ({{ github_repo_list }})
)
SELECT repo, key_id, title, created_at, last_used, read_only, age_days
FROM k
WHERE julianday(created_at) <= julianday('now', '-{{ deploy_key_max_age_days }} days')
