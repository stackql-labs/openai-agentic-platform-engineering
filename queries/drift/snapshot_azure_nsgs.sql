-- id: drift/snapshot_azure_nsgs
-- scenario: drift
-- providers: azure
-- params: azure_subscription_id
-- expected_columns: id, name, location, security_rules, tags
-- description: snapshot source - network security groups with their raw rules; materialized as snap_<ts>_azure_nsgs
SELECT id, name, location, security_rules, tags
FROM azure.network.network_security_groups
WHERE subscription_id = '{{ azure_subscription_id }}'
