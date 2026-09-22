-- id: triage/verify_asg_capacity
-- scenario: triage
-- providers: aws
-- params: aws_region, checkout_asg_name
-- expected_columns: auto_scaling_group_name, desired_capacity, instance_id, lifecycle_state, health_status
-- description: the post-mutation verification - desired capacity with one row per member instance; recovery means InService rows = desired_capacity (aggregation happens in code: StackQL cannot aggregate over a json_each CTE)
WITH g AS (
  SELECT auto_scaling_group_name, desired_capacity, instances
  FROM aws.autoscaling.auto_scaling_groups
  WHERE region = '{{ aws_region }}'
    AND auto_scaling_group_name = '{{ checkout_asg_name }}'
)
SELECT g.auto_scaling_group_name, g.desired_capacity,
  json_extract(i.value, '$.InstanceId') AS instance_id,
  json_extract(i.value, '$.LifecycleState') AS lifecycle_state,
  json_extract(i.value, '$.HealthStatus') AS health_status
FROM g, json_each(
  CASE WHEN json_valid(g.instances) = 0 THEN json_array()
       WHEN json_type(g.instances, '$.member') = 'array' THEN json_extract(g.instances, '$.member')
       WHEN json_type(g.instances, '$.member') = 'object' THEN json_array(json_extract(g.instances, '$.member'))
       ELSE json_array() END
) i
