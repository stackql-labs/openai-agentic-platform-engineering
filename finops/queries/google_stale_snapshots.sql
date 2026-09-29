-- id: finops/google_stale_snapshots
-- providers: google
-- params: google_project, snapshot_max_age_days
-- expected_columns: name, size_gb, storage_gb, created, labels, age_days, est_monthly_usd
-- description: disk snapshots older than the threshold, priced on stored bytes (0.026 USD/GB-month standard)
SELECT name, diskSizeGb AS size_gb,
  ROUND(CAST(storageBytes AS REAL) / 1073741824, 2) AS storage_gb,
  creationTimestamp AS created, labels,
  ROUND(julianday('now') - julianday(substr(creationTimestamp, 1, 19)), 0) AS age_days,
  ROUND(CAST(storageBytes AS REAL) / 1073741824 * 0.026, 2) AS est_monthly_usd
FROM google.compute.snapshots
WHERE project = '{{ google_project }}'
  AND julianday(substr(creationTimestamp, 1, 19)) < julianday('now', '-{{ snapshot_max_age_days }} days')
ORDER BY age_days DESC
