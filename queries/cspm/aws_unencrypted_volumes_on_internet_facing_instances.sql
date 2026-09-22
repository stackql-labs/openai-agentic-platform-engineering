-- id: cspm/aws_unencrypted_volumes_on_internet_facing_instances
-- scenario: cspm
-- providers: aws
-- params: aws_region
-- expected_columns: volume_id, size_gb, volume_type, encrypted, instance_id, instance_type, public_ip_address, tags
-- description: cross-resource join - unencrypted EBS volumes attached to instances that have a public IP address
WITH vols AS (
  SELECT volume_id, size, volume_type, encrypted, attachments
  FROM aws.ec2.volumes
  WHERE region = '{{ aws_region }}' AND state = 'in-use'
),
inst AS (
  SELECT instance_id, instance_type, public_ip_address, tags
  FROM aws.ec2.instances
  WHERE region = '{{ aws_region }}'
)
SELECT v.volume_id, v.size AS size_gb, v.volume_type, v.encrypted,
  i.instance_id, i.instance_type, i.public_ip_address, i.tags
FROM vols v
INNER JOIN inst i ON i.instance_id = json_extract(v.attachments, '$.item.instanceId')
WHERE v.encrypted IN ('false', '0', 0)
  AND COALESCE(i.public_ip_address, 'null') <> 'null'
