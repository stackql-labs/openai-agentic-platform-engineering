-- id: triage/aws_target_groups
-- scenario: triage
-- providers: aws
-- params: aws_region, demo_prefix
-- expected_columns: target_group_name, target_group_arn, load_balancer_arns
-- description: the checkout target group - its ARN feeds triage/aws_target_health
WITH t AS (
  SELECT target_group_name, target_group_arn, load_balancer_arns
  FROM aws.elbv2.target_groups
  WHERE region = '{{ aws_region }}'
)
SELECT target_group_name, target_group_arn, load_balancer_arns
FROM t
WHERE target_group_name = '{{ demo_prefix }}-checkout-tg'
