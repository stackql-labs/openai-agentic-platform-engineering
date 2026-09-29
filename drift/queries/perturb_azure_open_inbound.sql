-- id: drift/perturb_azure_open_inbound
-- providers: azure
-- params: azure_subscription_id, demo_prefix
-- expected_columns: (mutation - security_rules create_or_update)
-- kind: mutation
-- description: perturb - the out-of-band change: add an inbound Allow any/any rule from any source (priority 110) to the stack network security group; executed only by the perturb gate via run_mutation_query on a full_access server
INSERT INTO azure.network.security_rules(
   network_security_group_name,
   resource_group_name,
   security_rule_name,
   subscription_id,
   properties
)
SELECT
   '{{ demo_prefix }}-app-nsg',
   '{{ demo_prefix }}-drift-rg',
   'AllowAnyInbound',
   '{{ azure_subscription_id }}',
   '{"access": "Allow", "direction": "Inbound", "priority": 110, "protocol": "*", "sourceAddressPrefix": "*", "sourcePortRange": "*", "destinationAddressPrefix": "*", "destinationPortRange": "*", "description": "out-of-band change simulated by drift perturb"}'
