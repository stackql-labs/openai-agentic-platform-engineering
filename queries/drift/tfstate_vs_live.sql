-- id: drift/tfstate_vs_live
-- scenario: drift
-- providers: local
-- params: curr_view
-- expected_columns: resource_type, resource_key, tf_address, intended_attrs, live_attrs
-- description: terraform intended state (tfstate_resources, loaded by terraform_state_reader) against the latest live snapshot; rows are resources whose normalised attributes differ or that are missing on one side; plain SQL against snapshots/estate.db
SELECT t.resource_type, t.resource_key, t.tf_address, t.attrs_json AS intended_attrs, s.attrs_json AS live_attrs
FROM tfstate_resources t
LEFT JOIN {{ curr_view }} s ON s.resource_type = t.resource_type AND s.resource_key = t.resource_key
WHERE s.resource_key IS NULL OR s.attrs_json <> t.attrs_json
ORDER BY 1, 2
