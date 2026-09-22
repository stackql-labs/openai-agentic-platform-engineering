-- id: openai_estate/costs_by_project
-- scenario: openai_estate
-- providers: openai_admin
-- params: cost_start_time
-- expected_columns: bucket_start, project_id, line_item, amount_usd
-- description: organization costs since cost_start_time (unix seconds), unpacked per bucket and result line, by project where the API exposes it
WITH c AS (
  SELECT start_time, end_time, results
  FROM openai_admin.costs.costs
  WHERE start_time = '{{ cost_start_time }}'
)
SELECT datetime(c.start_time, 'unixepoch') AS bucket_start,
  COALESCE(json_extract(r.value, '$.project_id'), '(org)') AS project_id,
  COALESCE(json_extract(r.value, '$.line_item'), 'total') AS line_item,
  ROUND(CAST(json_extract(r.value, '$.amount.value') AS REAL), 4) AS amount_usd
FROM c, json_each(c.results) r
ORDER BY bucket_start, project_id
