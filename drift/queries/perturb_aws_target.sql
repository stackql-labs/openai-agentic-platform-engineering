-- id: drift/perturb_aws_target
-- providers: aws
-- params: aws_region, demo_prefix
-- expected_columns: group_id, group_name, vpc_id, tags
-- kind: select
-- description: perturb gate - locate the stack's security group by name and return its tags so code can assert the demo tag before the one mutation
SELECT group_id, group_name, vpc_id, tags
FROM aws.ec2.security_groups
WHERE region = '{{ aws_region }}'
AND group_name = '{{ demo_prefix }}-app-sg'
