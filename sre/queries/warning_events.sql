-- id: sre/warning_events
-- providers: k8s
-- params: k8s_namespace, kube_cluster_addr, kube_protocol
-- expected_columns: reason, message, count, last_timestamp, object_kind, object_name
-- description: Warning events in the demo namespace, newest first - scheduling, image pull, probe and OOM signals that would change the diagnosis
SELECT
  reason,
  message,
  count,
  last_timestamp,
  json_extract(involved_object, '$.kind') AS object_kind,
  json_extract(involved_object, '$.name') AS object_name
FROM k8s.core.events
WHERE namespace = '{{ k8s_namespace }}'
  AND cluster_addr = '{{ kube_cluster_addr }}'
  AND protocol = '{{ kube_protocol }}'
  AND type = 'Warning'
ORDER BY last_timestamp DESC
