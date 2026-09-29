-- id: entitlements/github_members_vs_idp
-- providers: github, entra_id
-- params: github_org, demo_prefix
-- expected_columns: login, idp_upn, idp_enabled, status
-- description: GitHub org members LEFT JOIN Entra ID users - orphans (no identity) and leavers (identity disabled); the join key is the IdP mailNickname <prefix>-<login>
-- idp: entra_id
WITH gh AS (
  SELECT login FROM github.orgs.members WHERE org = '{{ github_org }}'
),
idp AS (
  SELECT id, userPrincipalName AS upn, mailNickname AS nick, accountEnabled AS enabled
  FROM entra_id.users.users
)
SELECT gh.login, idp.upn AS idp_upn, idp.enabled AS idp_enabled,
  CASE WHEN idp.upn IS NULL THEN 'orphan: no IdP identity'
       WHEN idp.enabled IN ('false', '0', 0) THEN 'leaver: IdP account disabled'
       ELSE 'ok' END AS status
FROM gh
LEFT JOIN idp ON lower(idp.nick) = lower('{{ demo_prefix }}-' || gh.login)
ORDER BY status, gh.login
