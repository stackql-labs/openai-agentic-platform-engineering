-- id: sre/scale_deployment
-- providers: k8s
-- params: replicas, sre_target_deployment, k8s_namespace, kube_cluster_addr, kube_protocol
-- expected_columns:
-- description: THE mutation - patch the deployment scale subresource (k8s.apps.deployments_scale, merge-patch) to the approved replica count; reversible by running it again with the previous value. Only sre/src/gate.rs executes it, after approval and after sre/assert_demo_target passes
UPDATE k8s.apps.deployments_scale
SET spec = '{"replicas": {{ replicas }}}'
WHERE name = '{{ sre_target_deployment }}'
  AND namespace = '{{ k8s_namespace }}'
  AND cluster_addr = '{{ kube_cluster_addr }}'
  AND protocol = '{{ kube_protocol }}'
