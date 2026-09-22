-- id: triage/aws_asg_scale_out
-- scenario: triage
-- providers: aws
-- params: aws_region, checkout_asg_name, desired_capacity
-- expected_columns:
-- description: THE mutation - set the checkout ASG desired capacity (reversible: run again with the previous value). Only oape_agents/triage/gate.py may execute this, after approval and after triage/aws_asg_demo_tag_assert returns 1
UPDATE aws.autoscaling.auto_scaling_groups
SET desired_capacity = {{ desired_capacity }}
WHERE region = '{{ aws_region }}'
  AND auto_scaling_group_name = '{{ checkout_asg_name }}'
