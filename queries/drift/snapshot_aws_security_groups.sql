-- id: drift/snapshot_aws_security_groups
-- scenario: drift
-- providers: aws
-- params: aws_region
-- expected_columns: group_id, group_name, vpc_id, ip_permissions, tags
-- description: snapshot source - security groups with their raw ingress rules; materialized as snap_<ts>_aws_security_groups
SELECT group_id, group_name, vpc_id, ip_permissions, tags
FROM aws.ec2.security_groups
WHERE region = '{{ aws_region }}'
