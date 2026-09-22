-- id: smoke/azure_resource_groups
-- scenario: smoke
-- providers: azure
-- params: azure_subscription_id
-- expected_columns: name, location
-- description: resource groups in the demo subscription - proves azure auth
SELECT name, location
FROM azure.resource.resource_groups
WHERE subscription_id = '{{ azure_subscription_id }}'
