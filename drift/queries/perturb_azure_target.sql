-- id: drift/perturb_azure_target
-- providers: azure
-- params: azure_subscription_id, demo_prefix
-- expected_columns: name, location, tags
-- kind: select
-- description: perturb gate - fetch the stack's network security group and its tags so code can assert the demo tag before the one mutation
SELECT name, location, tags
FROM azure.network.network_security_groups
WHERE subscription_id = '{{ azure_subscription_id }}'
AND resource_group_name = '{{ demo_prefix }}-drift-rg'
AND network_security_group_name = '{{ demo_prefix }}-app-nsg'
