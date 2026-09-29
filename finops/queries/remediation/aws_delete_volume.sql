-- id: finops/remediation/aws_delete_volume
-- kind: mutation
-- providers: aws
-- params: aws_region, volume_id
-- description: delete one unattached EBS volume (aws.ec2.volumes delete_volume: volume_id, region)
DELETE FROM aws.ec2.volumes
WHERE volume_id = '{{ volume_id }}'
  AND region = '{{ aws_region }}'
