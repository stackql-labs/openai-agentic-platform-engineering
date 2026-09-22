-- id: openai_estate/usage_completions
-- scenario: openai_estate
-- providers: openai_admin
-- params: cost_start_time
-- expected_columns: bucket_start, project_id, model, input_tokens, output_tokens, requests
-- description: completions usage since cost_start_time (unix seconds) per bucket, project and model where the API groups it
WITH u AS (
  SELECT start_time, end_time, results
  FROM openai_admin.usage.completions
  WHERE start_time = '{{ cost_start_time }}'
)
SELECT datetime(u.start_time, 'unixepoch') AS bucket_start,
  COALESCE(json_extract(r.value, '$.project_id'), '(org)') AS project_id,
  COALESCE(json_extract(r.value, '$.model'), '(all)') AS model,
  json_extract(r.value, '$.input_tokens') AS input_tokens,
  json_extract(r.value, '$.output_tokens') AS output_tokens,
  json_extract(r.value, '$.num_model_requests') AS requests
FROM u, json_each(u.results) r
ORDER BY bucket_start, project_id
