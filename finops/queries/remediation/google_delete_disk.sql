-- id: finops/remediation/google_delete_disk
-- kind: mutation
-- providers: google
-- params: google_project, zone, disk
-- description: delete one unattached persistent disk (google.compute.disks delete: disk, project, zone - zone is the short name, not the URL)
DELETE FROM google.compute.disks
WHERE project = '{{ google_project }}'
  AND zone = '{{ zone }}'
  AND disk = '{{ disk }}'
