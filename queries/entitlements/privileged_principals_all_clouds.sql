-- id: entitlements/privileged_principals_all_clouds
-- scenario: entitlements
-- providers: aws, azure, google
-- params: aws_account_id, azure_subscription_id, google_project, aws_admins_sql
-- expected_columns: cloud, principal_type, principal, privilege, scope
-- description: cross-provider showpiece - every principal with the highest privilege in each cloud in one result set. AWS admins arrive as a literal UNION rendered from entitlements/aws_admin_user_literal over the users that cspm/aws_iam_user_attached_policies found with AdministratorAccess; Azure Owner assignments (role definition 8e3af657-a8ff-443c-a75c-2fe8c4bcb635) and GCP roles/owner bindings are live. One CTE level per provider, filters in the UNION branches (StackQL cannot nest CTEs)
WITH azure_ra AS (
  SELECT principal_id AS pid, principal_type AS ptype, scope AS sc, role_definition_id AS rdid
  FROM azure.authorization.role_assignments
  WHERE subscription_id = '{{ azure_subscription_id }}'
),
gcp_pol AS (
  SELECT role, members
  FROM google.cloudresourcemanager.projects_iam_policies
  WHERE projectsId = '{{ google_project }}'
    AND role = 'roles/owner'
),
aws_admins AS (
  {{ aws_admins_sql }}
)
SELECT 'aws' AS cloud, 'iam_user' AS principal_type, user_name AS principal,
  'AdministratorAccess' AS privilege, 'account {{ aws_account_id }}' AS scope
FROM aws_admins
UNION ALL
SELECT 'azure', ptype, pid, 'Owner', sc
FROM azure_ra
WHERE rdid LIKE '%/roleDefinitions/8e3af657-a8ff-443c-a75c-2fe8c4bcb635'
  AND sc = '/subscriptions/{{ azure_subscription_id }}'
UNION ALL
SELECT 'google',
  CASE WHEN m.value LIKE 'serviceAccount:%' THEN 'service_account'
       WHEN m.value LIKE 'user:%' THEN 'user'
       WHEN m.value LIKE 'group:%' THEN 'group' ELSE 'other' END,
  m.value, 'roles/owner', 'project {{ google_project }}'
FROM gcp_pol, json_each(gcp_pol.members) m
