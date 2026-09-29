-- id: entitlements/examples/github_members_vs_idp
-- providers: github, entra_id
-- params: github_org, demo_prefix
-- expected_columns: login, idp_upn, idp_enabled, status
-- description: the cross-provider join shape - GitHub org members LEFT JOIN Entra ID users on the demo naming convention (<prefix>-<login> as the IdP local part); a member with no match is an orphan, a match whose account is disabled is a leaver. With Okta as the IdP the same join reads the Okta users resource (subdomain in WHERE) and the login from its profile JSON. An example of the shape, not a pack.
SELECT gh.login, u.userPrincipalName AS idp_upn, u.accountEnabled AS idp_enabled,
  CASE WHEN u.userPrincipalName IS NULL THEN 'orphan: no IdP identity'
       WHEN u.accountEnabled IN ('false', '0', 0) THEN 'leaver: IdP account disabled'
       ELSE 'ok' END AS status
FROM github.orgs.members gh
LEFT JOIN entra_id.users.users u
  ON lower(u.mailNickname) = lower('{{ demo_prefix }}-' || gh.login)
WHERE gh.org = '{{ github_org }}'
ORDER BY status, gh.login
