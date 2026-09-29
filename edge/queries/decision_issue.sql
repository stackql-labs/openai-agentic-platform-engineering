-- id: edge/decision_issue
-- providers: github
-- params: github_owner, github_repo, issue_title, issue_body, issue_labels
-- expected_columns: none (mutation)
-- description: File the decision record as a GitHub issue in GITHUB_DECISIONS_REPO; rendered and executed only by the approval gate
INSERT INTO github.issues.issues (owner, repo, title, body, labels)
SELECT '{{ github_owner }}', '{{ github_repo }}', '{{ issue_title }}', '{{ issue_body }}', '{{ issue_labels }}'
