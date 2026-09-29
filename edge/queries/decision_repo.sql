-- id: edge/decision_repo
-- providers: github
-- params: github_owner, github_repo
-- expected_columns: full_name, has_issues, archived
-- description: Target assertion before the decision-record INSERT - the configured GITHUB_DECISIONS_REPO is reachable with the token and accepts issues
SELECT full_name, has_issues, archived
FROM github.repos.details WHERE owner = '{{ github_owner }}' AND repo = '{{ github_repo }}'
