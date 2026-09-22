-- id: entitlements/idp_privileged_group_members
-- scenario: entitlements
-- providers: entra_id
-- params: idp_privileged_group, idp_group_id
-- expected_columns: group_name, upn, display_name, account_enabled, status
-- description: members of the privileged group joined to user objects - disabled accounts still in the group are leavers with standing privilege
WITH mem AS (
  SELECT id FROM entra_id.groups.members WHERE group_id = '{{ idp_group_id }}'
),
u AS (
  SELECT id, userPrincipalName AS upn, displayName AS display_name, accountEnabled AS enabled
  FROM entra_id.users.users
)
SELECT '{{ idp_privileged_group }}' AS group_name, u.upn, u.display_name, u.enabled AS account_enabled,
  CASE WHEN u.enabled IN ('false', '0', 0) THEN 'leaver still privileged' ELSE 'active' END AS status
FROM mem
INNER JOIN u ON u.id = mem.id
ORDER BY status DESC, u.upn
