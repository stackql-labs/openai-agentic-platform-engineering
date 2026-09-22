-- id: drift/delta
-- scenario: drift
-- providers: local
-- params: prev_view, curr_view
-- expected_columns: change, provider, resource_type, resource_key, before_attrs, after_attrs
-- description: delta between two snapshots in the local backend (added / removed / changed on the normalised attribute JSON); runs as plain SQL against snapshots/estate.db, not against any cloud API
SELECT 'added' AS change, c.provider, c.resource_type, c.resource_key, NULL AS before_attrs, c.attrs_json AS after_attrs
FROM {{ curr_view }} c
LEFT JOIN {{ prev_view }} p ON p.resource_type = c.resource_type AND p.resource_key = c.resource_key
WHERE p.resource_key IS NULL
UNION ALL
SELECT 'removed', p.provider, p.resource_type, p.resource_key, p.attrs_json, NULL
FROM {{ prev_view }} p
LEFT JOIN {{ curr_view }} c ON c.resource_type = p.resource_type AND c.resource_key = p.resource_key
WHERE c.resource_key IS NULL
UNION ALL
SELECT 'changed', c.provider, c.resource_type, c.resource_key, p.attrs_json, c.attrs_json
FROM {{ curr_view }} c
INNER JOIN {{ prev_view }} p ON p.resource_type = c.resource_type AND p.resource_key = c.resource_key
WHERE p.attrs_json <> c.attrs_json
ORDER BY 2, 3, 4
