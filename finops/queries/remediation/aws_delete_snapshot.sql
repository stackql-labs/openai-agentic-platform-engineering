-- id: finops/remediation/aws_delete_snapshot
-- kind: mutation
-- providers: aws
-- params: aws_region, snapshot_id
-- description: delete one stale EBS snapshot (aws.ec2.snapshots delete_snapshot: snapshot_id, region)
DELETE FROM aws.ec2.snapshots
WHERE snapshot_id = '{{ snapshot_id }}'
  AND region = '{{ aws_region }}'
