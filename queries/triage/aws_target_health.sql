-- id: triage/aws_target_health
-- scenario: triage
-- providers: aws
-- params: aws_region, target_group_arn
-- expected_columns: target_id, port, state, reason, health_check_port
-- description: load balancer target health for the checkout target group (healthy / unhealthy / initial / draining)
SELECT json_extract(target, '$.Id') AS target_id,
  json_extract(target, '$.Port') AS port,
  json_extract(target_health, '$.State') AS state,
  json_extract(target_health, '$.Reason') AS reason,
  health_check_port
FROM aws.elbv2.target_healths
WHERE region = '{{ aws_region }}'
  AND target_group_arn = '{{ target_group_arn }}'
