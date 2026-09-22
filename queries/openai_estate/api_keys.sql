-- id: openai_estate/api_keys
-- scenario: openai_estate
-- providers: openai_admin
-- params: project_id, openai_key_max_age_days
-- expected_columns: project_id, id, name, owner_type, owner_name, created_at, last_used_at, age_days
-- description: project API keys (user and service account owned) with real age and last use; render_union over project ids
WITH k AS (
  SELECT '{{ project_id }}' AS project_id, id, name, owner, created_at, last_used_at
  FROM openai_admin.projects.api_keys
  WHERE project_id = '{{ project_id }}'
)
SELECT project_id, id, name,
  json_extract(owner, '$.type') AS owner_type,
  COALESCE(json_extract(owner, '$.service_account.name'), json_extract(owner, '$.user.name')) AS owner_name,
  created_at, last_used_at,
  CAST((julianday('now') - julianday(datetime(created_at, 'unixepoch'))) AS INTEGER) AS age_days
FROM k
WHERE (julianday('now') - julianday(datetime(created_at, 'unixepoch'))) >= {{ openai_key_max_age_days }}
