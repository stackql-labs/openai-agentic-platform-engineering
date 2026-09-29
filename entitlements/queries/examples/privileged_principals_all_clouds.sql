-- id: entitlements/examples/privileged_principals_all_clouds
-- providers: aws, azure, google
-- params: aws_admin_user, aws_iam_region, aws_account_id, azure_subscription_id, google_project
-- expected_columns: cloud, principal_type, principal, privilege, scope
-- description: the cross-cloud shape - one flat UNION ALL over three providers. The AWS branch is one member of a fan-out (attached_user_policies takes one user per call and does not echo it, so the user name is projected as a literal). The Azure branch lists by scope because any predicate on scope routes to the list-by-scope method; the scope column tells inherited assignments apart. The Google branch unnests the binding members with json_each. An example of the shape, not a pack.
SELECT 'aws' AS cloud, 'iam_user' AS principal_type, '{{ aws_admin_user }}' AS principal,
  policy_name AS privilege, 'account {{ aws_account_id }}' AS scope
FROM aws.iam.attached_user_policies
WHERE region = '{{ aws_iam_region }}'
  AND user_name = '{{ aws_admin_user }}'
  AND policy_arn = 'arn:aws:iam::aws:policy/AdministratorAccess'
UNION ALL
SELECT 'azure', principal_type, principal_id, 'Owner', scope
FROM azure.authorization.role_assignments
WHERE scope = '/subscriptions/{{ azure_subscription_id }}'
  AND role_definition_id LIKE '%/roleDefinitions/8e3af657-a8ff-443c-a75c-2fe8c4bcb635'
UNION ALL
SELECT 'google',
  CASE WHEN m.value LIKE 'serviceAccount:%' THEN 'service_account'
       WHEN m.value LIKE 'user:%' THEN 'user'
       WHEN m.value LIKE 'group:%' THEN 'group'
       ELSE 'other' END,
  m.value, 'roles/owner', 'project {{ google_project }}'
FROM google.cloudresourcemanager.projects_iam_policies p, json_each(p.members) m
WHERE p.projectsId = '{{ google_project }}'
  AND p.role = 'roles/owner'
