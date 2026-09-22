-- id: finops/aws_unassociated_eips
-- scenario: finops
-- providers: aws
-- params: aws_region
-- expected_columns: public_ip, allocation_id, domain, tags, est_monthly_usd
-- description: Elastic IPs allocated but not associated with anything (billed hourly while idle; 0.005 USD/hour = 3.65 USD/month)
WITH a AS (
  SELECT public_ip, allocation_id, association_id, domain, tags
  FROM aws.ec2.addresses
  WHERE region = '{{ aws_region }}'
)
SELECT public_ip, allocation_id, domain, tags, 3.65 AS est_monthly_usd
FROM a
WHERE COALESCE(association_id, 'null') = 'null'
