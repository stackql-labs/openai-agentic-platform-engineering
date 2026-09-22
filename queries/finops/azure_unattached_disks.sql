-- id: finops/azure_unattached_disks
-- scenario: finops
-- providers: azure
-- params: azure_subscription_id
-- expected_columns: name, id, location, disk_size_gb, sku, disk_state, time_created, tags, est_monthly_usd
-- description: managed disks in state Unattached with a list-price estimate (USD/GB-month approx: Premium 0.135, StandardSSD 0.075, Standard HDD 0.045)
WITH d AS (
  SELECT name, id, location, disk_size_gb, json_extract(sku, '$.name') AS sku, disk_state, time_created, tags
  FROM azure.compute.disks
  WHERE subscription_id = '{{ azure_subscription_id }}'
)
SELECT name, id, location, disk_size_gb, sku, disk_state, time_created, tags,
  ROUND(disk_size_gb * CASE WHEN sku LIKE 'Premium%' THEN 0.135 WHEN sku LIKE 'StandardSSD%' THEN 0.075 ELSE 0.045 END, 2) AS est_monthly_usd
FROM d
WHERE disk_state = 'Unattached'
ORDER BY est_monthly_usd DESC
