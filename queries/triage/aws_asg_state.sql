-- id: triage/aws_asg_state
-- scenario: triage
-- providers: aws
-- params: aws_region, checkout_asg_name
-- expected_columns: auto_scaling_group_name, desired_capacity, min_size, max_size, instance_id, lifecycle_state, health_status, availability_zone
-- description: the checkout auto scaling group with one row per member instance (capacity vs what is actually in service)
WITH g AS (
  SELECT auto_scaling_group_name, desired_capacity, min_size, max_size, instances
  FROM aws.autoscaling.auto_scaling_groups
  WHERE region = '{{ aws_region }}'
    AND auto_scaling_group_name = '{{ checkout_asg_name }}'
)
SELECT g.auto_scaling_group_name, g.desired_capacity, g.min_size, g.max_size,
  json_extract(i.value, '$.InstanceId') AS instance_id,
  json_extract(i.value, '$.LifecycleState') AS lifecycle_state,
  json_extract(i.value, '$.HealthStatus') AS health_status,
  json_extract(i.value, '$.AvailabilityZone') AS availability_zone
FROM g, json_each(
  CASE WHEN json_valid(g.instances) = 0 THEN json_array()
       WHEN json_type(g.instances, '$.member') = 'array' THEN json_extract(g.instances, '$.member')
       WHEN json_type(g.instances, '$.member') = 'object' THEN json_array(json_extract(g.instances, '$.member'))
       ELSE json_array() END
) i
