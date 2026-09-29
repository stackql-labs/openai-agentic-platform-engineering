-- id: finops/remediation/azure_delete_public_ip
-- kind: mutation
-- providers: azure
-- params: azure_subscription_id, resource_group_name, public_ip_address_name
-- description: delete one unassociated public IP (azure.network.public_ip_addresses delete: public_ip_address_name, resource_group_name, subscription_id)
DELETE FROM azure.network.public_ip_addresses
WHERE subscription_id = '{{ azure_subscription_id }}'
  AND resource_group_name = '{{ resource_group_name }}'
  AND public_ip_address_name = '{{ public_ip_address_name }}'
