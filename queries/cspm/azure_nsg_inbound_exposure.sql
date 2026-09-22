-- id: cspm/azure_nsg_inbound_exposure
-- scenario: cspm
-- providers: azure
-- params: azure_subscription_id
-- expected_columns: nsg, id, location, rule, priority, direction, access, protocol, port, source, from_internet, tags
-- description: every NSG security rule with from_internet computed in SQL (1 = inbound Allow whose source is *, 0.0.0.0/0 or Internet). Consumers filter from_internet = 1 (see cspm/aws_security_group_ingress_exposure for why the classification is a column)
WITH nsg AS (
  SELECT name, id, location, security_rules, tags
  FROM azure.network.network_security_groups
  WHERE subscription_id = '{{ azure_subscription_id }}'
)
SELECT nsg.name AS nsg, nsg.id, nsg.location,
  json_extract(r.value, '$.name') AS rule,
  json_extract(r.value, '$.properties.priority') AS priority,
  json_extract(r.value, '$.properties.direction') AS direction,
  json_extract(r.value, '$.properties.access') AS access,
  json_extract(r.value, '$.properties.protocol') AS protocol,
  json_extract(r.value, '$.properties.destinationPortRange') AS port,
  json_extract(r.value, '$.properties.sourceAddressPrefix') AS source,
  CASE WHEN json_extract(r.value, '$.properties.direction') = 'Inbound'
        AND json_extract(r.value, '$.properties.access') = 'Allow'
        AND json_extract(r.value, '$.properties.sourceAddressPrefix') IN ('*', '0.0.0.0/0', 'Internet')
       THEN 1 ELSE 0 END AS from_internet,
  nsg.tags
FROM nsg, json_each(nsg.security_rules) r
