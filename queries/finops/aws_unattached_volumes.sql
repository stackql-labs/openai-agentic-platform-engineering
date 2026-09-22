-- id: finops/aws_unattached_volumes
-- scenario: finops
-- providers: aws
-- params: aws_region
-- expected_columns: volume_id, size_gb, volume_type, availability_zone, create_time, encrypted, tags, est_monthly_usd
-- description: EBS volumes in state available (attached to nothing) with a list-price monthly estimate (USD/GB-month: gp3 0.096, gp2 0.12, io1/io2 0.15, st1 0.054, sc1 0.018)
SELECT volume_id, size AS size_gb, volume_type, availability_zone, create_time, encrypted, tags,
  ROUND(size * CASE volume_type
    WHEN 'gp3' THEN 0.096 WHEN 'gp2' THEN 0.12 WHEN 'io1' THEN 0.15 WHEN 'io2' THEN 0.15
    WHEN 'st1' THEN 0.054 WHEN 'sc1' THEN 0.018 ELSE 0.10 END, 2) AS est_monthly_usd
FROM aws.ec2.volumes
WHERE region = '{{ aws_region }}'
  AND state = 'available'
ORDER BY est_monthly_usd DESC
