-- id: finops/remediation/azure_delete_disk
-- kind: mutation
-- providers: azure
-- params: azure_subscription_id, resource_group_name, disk_name
-- description: delete one unattached managed disk (azure.compute.disks delete: disk_name, resource_group_name, subscription_id)
DELETE FROM azure.compute.disks
WHERE subscription_id = '{{ azure_subscription_id }}'
  AND resource_group_name = '{{ resource_group_name }}'
  AND disk_name = '{{ disk_name }}'
