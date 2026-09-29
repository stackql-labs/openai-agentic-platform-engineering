-- id: sre/reset_scale
-- providers: k8s
-- params: replicas, sre_target_deployment, k8s_namespace, kube_cluster_addr, kube_protocol
-- expected_columns:
-- description: code-owned - the only mutation template left in the repository, used by `sre run --reset` to put the demo deployment back to one replica through the same gate; no model is involved. The model-proposed scale statement is discovered at run time from describe_method and checked against the gate allowlist instead
UPDATE k8s.apps.deployments_scale
SET spec = '{"replicas": {{ replicas }}}'
WHERE name = '{{ sre_target_deployment }}'
  AND namespace = '{{ k8s_namespace }}'
  AND cluster_addr = '{{ kube_cluster_addr }}'
  AND protocol = '{{ kube_protocol }}'
