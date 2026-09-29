-- id: sre/pods_for_deployment
-- providers: k8s
-- params: sre_app_label, k8s_namespace, kube_cluster_addr, kube_protocol
-- expected_columns: pod, phase, ready, restarts, node, start_time
-- description: the pods behind the target deployment (label app=<sre_app_label>) - phase, container readiness and restart count per pod
SELECT
  json_extract(metadata, '$.name') AS pod,
  json_extract(status, '$.phase') AS phase,
  json_extract(status, '$.containerStatuses[0].ready') AS ready,
  json_extract(status, '$.containerStatuses[0].restartCount') AS restarts,
  json_extract(spec, '$.nodeName') AS node,
  json_extract(status, '$.startTime') AS start_time
FROM k8s.core.pods
WHERE namespace = '{{ k8s_namespace }}'
  AND cluster_addr = '{{ kube_cluster_addr }}'
  AND protocol = '{{ kube_protocol }}'
  AND json_extract(metadata, '$.labels.app') = '{{ sre_app_label }}'
