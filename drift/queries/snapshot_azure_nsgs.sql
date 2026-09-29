-- id: drift/snapshot_azure_nsgs
-- providers: azure
-- params: azure_subscription_id
-- expected_columns: id, name, location, security_rules, tags
-- kind: select
-- description: snapshot source - network security groups in the demo subscription with their raw rules and tags; normalised as resource_type network_security_group
SELECT id, name, location, security_rules, tags
FROM azure.network.network_security_groups
WHERE subscription_id = '{{ azure_subscription_id }}'
