-- id: entitlements/okta_github_members_vs_idp
-- providers: github, okta
-- params: github_org, demo_prefix, okta_subdomain
-- expected_columns: login, idp_login, idp_status, status
-- description: GitHub org members LEFT JOIN Okta users - orphans (no identity) and leavers (DEPROVISIONED or SUSPENDED); the join key is the Okta profile.login local part <prefix>-<login>@<domain>
-- idp: okta
WITH gh AS (
  SELECT login FROM github.orgs.members WHERE org = '{{ github_org }}'
),
idp AS (
  SELECT id, status, JSON_EXTRACT(profile, '$.login') AS idp_login
  FROM okta.users.users
  WHERE subdomain = '{{ okta_subdomain }}'
)
SELECT gh.login, idp.idp_login, idp.status AS idp_status,
  CASE WHEN idp.idp_login IS NULL THEN 'orphan: no IdP identity'
       WHEN idp.status IN ('DEPROVISIONED', 'SUSPENDED') THEN 'leaver: IdP account disabled'
       ELSE 'ok' END AS status
FROM gh
LEFT JOIN idp ON lower(idp.idp_login) LIKE lower('{{ demo_prefix }}-' || gh.login || '@%')
ORDER BY status, gh.login
