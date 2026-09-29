-- id: drift/snapshot_aws_instances
-- providers: aws
-- params: aws_region
-- expected_columns: instance_id, instance_type, state, tags, security_groups
-- kind: select
-- description: snapshot source - EC2 instances in the demo region (the stack creates none; any row here is itself drift); normalised as resource_type ec2_instance
SELECT instance_id, instance_type, state, tags, security_groups
FROM aws.ec2.instances
WHERE region = '{{ aws_region }}'
