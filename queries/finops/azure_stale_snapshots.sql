-- id: finops/azure_stale_snapshots
-- scenario: finops
-- providers: azure
-- params: azure_subscription_id, snapshot_max_age_days
-- expected_columns: name, id, location, disk_size_gb, time_created, tags, age_days, est_monthly_usd
-- description: managed disk snapshots older than the threshold (0.05 USD/GB-month standard)
WITH s AS (
  SELECT name, id, location, disk_size_gb, time_created, tags
  FROM azure.compute.snapshots
  WHERE subscription_id = '{{ azure_subscription_id }}'
)
SELECT name, id, location, disk_size_gb, time_created, tags,
  ROUND(julianday('now') - julianday(substr(time_created, 1, 19)), 0) AS age_days,
  ROUND(disk_size_gb * 0.05, 2) AS est_monthly_usd
FROM s
WHERE julianday(substr(time_created, 1, 19)) < julianday('now', '-{{ snapshot_max_age_days }} days')
ORDER BY age_days DESC
