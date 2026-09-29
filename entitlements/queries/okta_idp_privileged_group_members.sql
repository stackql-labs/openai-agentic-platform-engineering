-- id: entitlements/okta_idp_privileged_group_members
-- providers: okta
-- params: idp_privileged_group, idp_group_id, okta_subdomain
-- expected_columns: group_name, upn, display_name, account_status, status
-- description: members of the privileged Okta group (okta.groups.users returns the full user objects, so no second join is needed) - DEPROVISIONED or SUSPENDED accounts still in the group are leavers with standing privilege
-- idp: okta
WITH mem AS (
  SELECT id, status AS acct_status, JSON_EXTRACT(profile, '$.login') AS upn,
    JSON_EXTRACT(profile, '$.firstName') || ' ' || JSON_EXTRACT(profile, '$.lastName') AS display_name
  FROM okta.groups.users
  WHERE groupId = '{{ idp_group_id }}'
    AND subdomain = '{{ okta_subdomain }}'
)
SELECT '{{ idp_privileged_group }}' AS group_name, upn, display_name, acct_status AS account_status,
  CASE WHEN acct_status IN ('DEPROVISIONED', 'SUSPENDED') THEN 'leaver still privileged' ELSE 'active' END AS status
FROM mem
ORDER BY status DESC, upn
