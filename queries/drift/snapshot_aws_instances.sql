-- id: drift/snapshot_aws_instances
-- scenario: drift
-- providers: aws
-- params: aws_region
-- expected_columns: instance_id, instance_type, state, tags, security_groups
-- description: snapshot source - EC2 instances in the demo region; materialized into the local backend as snap_<ts>_aws_instances
SELECT instance_id, instance_type, json_extract(state, '$.name') AS state, tags, security_groups
FROM aws.ec2.instances
WHERE region = '{{ aws_region }}'
