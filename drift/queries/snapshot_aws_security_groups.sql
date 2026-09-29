-- id: drift/snapshot_aws_security_groups
-- providers: aws
-- params: aws_region
-- expected_columns: group_id, group_name, vpc_id, ip_permissions, tags
-- kind: select
-- description: snapshot source - security groups in the demo region with their raw ingress rules and tags; normalised into snapshot_<ts> as resource_type security_group
SELECT group_id, group_name, vpc_id, ip_permissions, tags
FROM aws.ec2.security_groups
WHERE region = '{{ aws_region }}'
