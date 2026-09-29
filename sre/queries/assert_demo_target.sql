-- id: sre/assert_demo_target
-- providers: k8s
-- params: sre_target_deployment, k8s_namespace, kube_cluster_addr, kube_protocol, demo_tag_key
-- expected_columns: name, namespace, demo_label
-- description: the pre-mutation assertion run by sre/src/gate.rs on the executor server - the target must exist in K8S_NAMESPACE, carry the DEMO_PREFIX name and the demo label, or nothing executes
SELECT
  json_extract(metadata, '$.name') AS name,
  json_extract(metadata, '$.namespace') AS namespace,
  json_extract(metadata, '$.labels.{{ demo_tag_key }}') AS demo_label
FROM k8s.apps.deployments
WHERE name = '{{ sre_target_deployment }}'
  AND namespace = '{{ k8s_namespace }}'
  AND cluster_addr = '{{ kube_cluster_addr }}'
  AND protocol = '{{ kube_protocol }}'
