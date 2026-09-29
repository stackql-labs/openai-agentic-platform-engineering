-- id: sre/verify_replicas
-- providers: k8s
-- params: sre_target_deployment, k8s_namespace, kube_cluster_addr, kube_protocol
-- expected_columns: name, spec_replicas, ready_replicas, available_replicas
-- description: code-owned - the post-mutation verification polled by code when the model-proposed verification SELECT fails validation or lacks the spec_replicas and ready_replicas columns
SELECT
  json_extract(metadata, '$.name') AS name,
  json_extract(spec, '$.replicas') AS spec_replicas,
  json_extract(status, '$.readyReplicas') AS ready_replicas,
  json_extract(status, '$.availableReplicas') AS available_replicas
FROM k8s.apps.deployments
WHERE name = '{{ sre_target_deployment }}'
  AND namespace = '{{ k8s_namespace }}'
  AND cluster_addr = '{{ kube_cluster_addr }}'
  AND protocol = '{{ kube_protocol }}'
