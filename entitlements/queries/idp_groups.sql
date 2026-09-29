-- id: entitlements/idp_groups
-- providers: entra_id
-- params: idp_privileged_group
-- expected_columns: id, display_name
-- description: the privileged Entra ID group by display name - its id feeds entitlements/idp_privileged_group_members
-- idp: entra_id
WITH g AS (
  SELECT id, displayName AS display_name FROM entra_id.groups.groups
)
SELECT id, display_name FROM g WHERE display_name = '{{ idp_privileged_group }}'
