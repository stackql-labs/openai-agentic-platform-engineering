-- id: drift/perturb_azure_restore
-- providers: azure
-- params: azure_subscription_id, demo_prefix
-- expected_columns: (mutation - security_rules delete)
-- kind: mutation
-- description: perturb --restore - delete the AllowAnyInbound rule from the stack network security group
DELETE FROM azure.network.security_rules
WHERE subscription_id = '{{ azure_subscription_id }}'
AND resource_group_name = '{{ demo_prefix }}-drift-rg'
AND network_security_group_name = '{{ demo_prefix }}-app-nsg'
AND security_rule_name = 'AllowAnyInbound'
