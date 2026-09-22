-- id: openai_estate/projects
-- scenario: openai_estate
-- providers: openai_admin
-- params:
-- expected_columns: id, name, status, created_at, archived_at
-- description: every project in the OpenAI organization (needs OPENAI_ADMIN_KEY)
SELECT id, name, status, created_at, archived_at
FROM openai_admin.projects.projects
ORDER BY created_at DESC
