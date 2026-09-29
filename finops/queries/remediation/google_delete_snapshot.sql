-- id: finops/remediation/google_delete_snapshot
-- kind: mutation
-- providers: google
-- params: google_project, snapshot
-- description: delete one stale disk snapshot (google.compute.snapshots delete: snapshot, project)
DELETE FROM google.compute.snapshots
WHERE project = '{{ google_project }}'
  AND snapshot = '{{ snapshot }}'
