-- id: entitlements/okta_idp_groups
-- providers: okta
-- params: idp_privileged_group, okta_subdomain
-- expected_columns: id, display_name
-- description: the privileged Okta group by profile.name - its id feeds entitlements/okta_idp_privileged_group_members
-- idp: okta
WITH g AS (
  SELECT id, JSON_EXTRACT(profile, '$.name') AS display_name
  FROM okta.groups.groups
  WHERE subdomain = '{{ okta_subdomain }}'
)
SELECT id, display_name FROM g WHERE display_name = '{{ idp_privileged_group }}'
