-- id: openai_estate/service_accounts
-- scenario: openai_estate
-- providers: openai_admin
-- params: project_id, openai_key_max_age_days
-- expected_columns: project_id, id, name, role, created_at, age_days
-- description: service accounts in one project with real age against the threshold; render_union over project ids (the API does not echo the project)
WITH sa AS (
  SELECT '{{ project_id }}' AS project_id, id, name, role, created_at
  FROM openai_admin.projects.service_accounts
  WHERE project_id = '{{ project_id }}'
)
SELECT project_id, id, name, role, created_at,
  CAST((julianday('now') - julianday(datetime(created_at, 'unixepoch'))) AS INTEGER) AS age_days
FROM sa
WHERE (julianday('now') - julianday(datetime(created_at, 'unixepoch'))) >= {{ openai_key_max_age_days }}
