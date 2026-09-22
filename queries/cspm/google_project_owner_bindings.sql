-- id: cspm/google_project_owner_bindings
-- scenario: cspm
-- providers: google
-- params: google_project
-- expected_columns: project, role, member, member_type
-- description: every principal bound to roles/owner on the project, one row per member (the role filter sits in the provider query: predicates on the unnested members are not applied)
WITH pol AS (
  SELECT role, members
  FROM google.cloudresourcemanager.projects_iam_policies
  WHERE projectsId = '{{ google_project }}'
    AND role = 'roles/owner'
)
SELECT '{{ google_project }}' AS project, pol.role, m.value AS member,
  CASE WHEN m.value LIKE 'serviceAccount:%' THEN 'service_account'
       WHEN m.value LIKE 'user:%' THEN 'user'
       WHEN m.value LIKE 'group:%' THEN 'group'
       ELSE 'other' END AS member_type
FROM pol, json_each(pol.members) m
