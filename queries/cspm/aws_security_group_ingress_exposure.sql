-- id: cspm/aws_security_group_ingress_exposure
-- scenario: cspm
-- providers: aws
-- params: aws_region
-- expected_columns: group_id, group_name, vpc_id, protocol, from_port, to_port, ip_ranges, open_to_world, tags
-- description: every security group ingress rule with open_to_world computed in SQL (1 = reachable from 0.0.0.0/0 on 22, 3389 or all protocols). Consumers filter open_to_world = 1: StackQL does not apply WHERE predicates over json_each output of a live provider table, so the classification is a projected column
WITH sg AS (
  SELECT group_id, group_name, vpc_id, ip_permissions, tags
  FROM aws.ec2.security_groups
  WHERE region = '{{ aws_region }}'
)
SELECT sg.group_id, sg.group_name, sg.vpc_id,
  json_extract(p.value, '$.ipProtocol') AS protocol,
  json_extract(p.value, '$.fromPort') AS from_port,
  json_extract(p.value, '$.toPort') AS to_port,
  json_extract(p.value, '$.ipRanges') AS ip_ranges,
  CASE WHEN json_extract(p.value, '$.ipRanges') LIKE '%0.0.0.0/0%'
       THEN CASE WHEN json_extract(p.value, '$.ipProtocol') = '-1' THEN 1
                 WHEN json_extract(p.value, '$.fromPort') IN ('22', '3389', 22, 3389) THEN 1
                 ELSE 0 END
       ELSE 0 END AS open_to_world,
  sg.tags
FROM sg, json_each(
  CASE WHEN json_valid(sg.ip_permissions) = 0 THEN json_array()
       WHEN json_type(sg.ip_permissions, '$.item') = 'array' THEN json_extract(sg.ip_permissions, '$.item')
       WHEN json_type(sg.ip_permissions, '$.item') = 'object' THEN json_array(json_extract(sg.ip_permissions, '$.item'))
       ELSE json_array() END
) p
