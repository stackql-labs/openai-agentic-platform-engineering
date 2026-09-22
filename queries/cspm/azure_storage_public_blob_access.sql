-- id: cspm/azure_storage_public_blob_access
-- scenario: cspm
-- providers: azure
-- params: azure_subscription_id
-- expected_columns: name, id, location, allow_blob_public_access, minimum_tls_version, tags
-- description: storage accounts that allow anonymous public blob access
SELECT name, id, location, allow_blob_public_access, minimum_tls_version, tags
FROM azure.storage.storage_accounts
WHERE subscription_id = '{{ azure_subscription_id }}'
  AND allow_blob_public_access IN ('true', '1', 1)
