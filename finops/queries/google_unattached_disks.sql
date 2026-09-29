-- id: finops/google_unattached_disks
-- providers: google
-- params: google_project
-- expected_columns: name, zone, size_gb, type, status, created, labels, est_monthly_usd
-- description: persistent disks with no users (attached to nothing) across all zones with a list-price estimate (USD/GB-month: pd-ssd 0.17, pd-balanced 0.10, pd-standard 0.04)
SELECT name, zone, sizeGb AS size_gb, type, status, creationTimestamp AS created, labels,
  ROUND(CAST(sizeGb AS REAL) * CASE
    WHEN type LIKE '%pd-ssd' THEN 0.17
    WHEN type LIKE '%pd-balanced' THEN 0.10
    ELSE 0.04 END, 2) AS est_monthly_usd
FROM google.compute.disks
WHERE project = '{{ google_project }}'
  AND COALESCE(users, 'null') IN ('null', '[]')
  AND status = 'READY'
ORDER BY est_monthly_usd DESC
