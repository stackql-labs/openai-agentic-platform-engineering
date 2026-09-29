-- id: finops/aws_unassociated_eips
-- providers: aws
-- params: aws_region
-- expected_columns: public_ip, allocation_id, domain, tags, est_monthly_usd
-- description: Elastic IPs allocated but not associated with anything (billed hourly while idle; 0.005 USD/hour = 3.65 USD/month)
SELECT public_ip, allocation_id, domain, tags, 3.65 AS est_monthly_usd
FROM aws.ec2.addresses
WHERE region = '{{ aws_region }}'
  AND COALESCE(association_id, 'null') = 'null'
