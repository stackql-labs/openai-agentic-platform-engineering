-- id: triage/aws_service_instances
-- scenario: triage
-- providers: aws
-- params: aws_region, checkout_service_tag
-- expected_columns: instance_id, instance_type, launch_time, state, private_ip_address, public_ip_address, tags
-- description: instances tagged service=<checkout_service_tag> with their state and launch time
WITH i AS (
  SELECT instance_id, instance_type, launch_time, json_extract(state, '$.name') AS state,
    private_ip_address, public_ip_address, tags
  FROM aws.ec2.instances
  WHERE region = '{{ aws_region }}'
)
SELECT instance_id, instance_type, launch_time, state, private_ip_address, public_ip_address, tags
FROM i
WHERE tags LIKE '%service%{{ checkout_service_tag }}%'
ORDER BY launch_time DESC
