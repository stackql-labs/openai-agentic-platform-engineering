-- id: finops/google_stale_snapshots
-- scenario: finops
-- providers: google
-- params: google_project, snapshot_max_age_days
-- expected_columns: name, size_gb, storage_gb, created, labels, age_days, est_monthly_usd
-- description: disk snapshots older than the threshold, priced on stored bytes (0.026 USD/GB-month standard)
WITH s AS (
  SELECT name, diskSizeGb AS size_gb, storageBytes AS storage_bytes, creationTimestamp AS created, labels
  FROM google.compute.snapshots
  WHERE project = '{{ google_project }}'
)
SELECT name, size_gb, ROUND(CAST(storage_bytes AS REAL) / 1073741824, 2) AS storage_gb, created, labels,
  ROUND(julianday('now') - julianday(substr(created, 1, 19)), 0) AS age_days,
  ROUND(CAST(storage_bytes AS REAL) / 1073741824 * 0.026, 2) AS est_monthly_usd
FROM s
WHERE julianday(substr(created, 1, 19)) < julianday('now', '-{{ snapshot_max_age_days }} days')
ORDER BY age_days DESC
