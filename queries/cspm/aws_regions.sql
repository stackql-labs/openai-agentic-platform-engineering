-- id: cspm/aws_regions
-- scenario: cspm
-- providers: aws
-- params: aws_region
-- expected_columns: region_name, opt_in_status
-- description: enabled regions - the fan-out list for region-swept checks
SELECT region_name, opt_in_status
FROM aws.ec2.regions
WHERE region = '{{ aws_region }}'
ORDER BY region_name
