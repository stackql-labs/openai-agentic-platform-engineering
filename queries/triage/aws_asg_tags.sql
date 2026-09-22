-- id: triage/aws_asg_tags
-- scenario: triage
-- providers: aws
-- params: aws_region, checkout_asg_name
-- expected_columns: auto_scaling_group_name, tag_key, tag_value
-- description: the tags on the target ASG, one row each - the pre-mutation assertion in oape_agents/triage/gate.py requires a row with tag_key = DEMO_TAG_KEY and tag_value = DEMO_TAG_VALUE before anything executes
WITH g AS (
  SELECT auto_scaling_group_name, tags
  FROM aws.autoscaling.auto_scaling_groups
  WHERE region = '{{ aws_region }}'
    AND auto_scaling_group_name = '{{ checkout_asg_name }}'
)
SELECT g.auto_scaling_group_name,
  json_extract(x.value, '$.Key') AS tag_key,
  json_extract(x.value, '$.Value') AS tag_value
FROM g, json_each(
  CASE WHEN json_valid(g.tags) = 0 THEN json_array()
       WHEN json_type(g.tags, '$.member') = 'array' THEN json_extract(g.tags, '$.member')
       WHEN json_type(g.tags, '$.member') = 'object' THEN json_array(json_extract(g.tags, '$.member'))
       ELSE json_array() END
) x
