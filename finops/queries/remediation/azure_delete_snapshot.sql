-- id: finops/remediation/azure_delete_snapshot
-- kind: mutation
-- providers: azure
-- params: azure_subscription_id, resource_group_name, snapshot_name
-- description: delete one stale managed disk snapshot (azure.compute.snapshots delete: snapshot_name, resource_group_name, subscription_id)
DELETE FROM azure.compute.snapshots
WHERE subscription_id = '{{ azure_subscription_id }}'
  AND resource_group_name = '{{ resource_group_name }}'
  AND snapshot_name = '{{ snapshot_name }}'
