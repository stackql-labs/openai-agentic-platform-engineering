-- id: sre/deployment_state
-- providers: k8s
-- params: sre_target_deployment, k8s_namespace, kube_cluster_addr, kube_protocol
-- expected_columns: name, namespace, spec_replicas, status_replicas, ready_replicas, available_replicas, unavailable_replicas, labels
-- description: the target deployment - desired replicas (spec) against what is ready and available (status); the primary capacity signal for the diagnosis
SELECT
  json_extract(metadata, '$.name') AS name,
  json_extract(metadata, '$.namespace') AS namespace,
  json_extract(spec, '$.replicas') AS spec_replicas,
  json_extract(status, '$.replicas') AS status_replicas,
  json_extract(status, '$.readyReplicas') AS ready_replicas,
  json_extract(status, '$.availableReplicas') AS available_replicas,
  json_extract(status, '$.unavailableReplicas') AS unavailable_replicas,
  json_extract(metadata, '$.labels') AS labels
FROM k8s.apps.deployments
WHERE name = '{{ sre_target_deployment }}'
  AND namespace = '{{ k8s_namespace }}'
  AND cluster_addr = '{{ kube_cluster_addr }}'
  AND protocol = '{{ kube_protocol }}'
