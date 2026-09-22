-- id: cspm/azure_owner_assignments_subscription_scope
-- scenario: cspm
-- providers: azure
-- params: azure_subscription_id
-- expected_columns: assignment_id, principal_id, principal_type, scope, created_on
-- description: role assignments granting the built-in Owner role (definition 8e3af657-a8ff-443c-a75c-2fe8c4bcb635) directly at subscription scope
WITH ra AS (
  SELECT id AS assignment_id, principal_id AS pid, principal_type AS ptype, scope AS sc,
    role_definition_id AS rdid, created_on AS created
  FROM azure.authorization.role_assignments
  WHERE subscription_id = '{{ azure_subscription_id }}'
)
SELECT assignment_id, pid AS principal_id, ptype AS principal_type, sc AS scope, created AS created_on
FROM ra
WHERE rdid LIKE '%/roleDefinitions/8e3af657-a8ff-443c-a75c-2fe8c4bcb635'
  AND sc = '/subscriptions/{{ azure_subscription_id }}'
