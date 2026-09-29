-- id: finops/examples/azure_unassociated_public_ips
-- providers: azure
-- params: azure_subscription_id
-- expected_columns: name, id, location, ip_address, sku, allocation_method, tags, est_monthly_usd
-- description: public IP addresses with no IP configuration (associated with nothing); a Standard static IP idles at about 3.65 USD/month
SELECT name, id, location, ip_address, json_extract(sku, '$.name') AS sku,
  public_ip_allocation_method AS allocation_method, tags, 3.65 AS est_monthly_usd
FROM azure.network.public_ip_addresses
WHERE subscription_id = '{{ azure_subscription_id }}'
  AND COALESCE(ip_configuration, 'null') = 'null'
