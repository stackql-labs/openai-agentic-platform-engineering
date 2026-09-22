-- id: triage/github_recent_deployments
-- scenario: triage
-- providers: github
-- params: github_org, checkout_repo, recent_deploy_hours
-- expected_columns: id, environment, ref, description, created_at, hours_ago
-- description: deployments of the checkout repo in the last recent_deploy_hours - the change-correlation input for diagnosis
WITH d AS (
  SELECT id, environment, ref, description, created_at
  FROM github.repos.deployments
  WHERE owner = '{{ github_org }}'
    AND repo = '{{ checkout_repo }}'
)
SELECT id, environment, ref, description, created_at,
  ROUND((julianday('now') - julianday(created_at)) * 24, 1) AS hours_ago
FROM d
WHERE julianday(created_at) >= julianday('now', '-{{ recent_deploy_hours }} hours')
ORDER BY created_at DESC
