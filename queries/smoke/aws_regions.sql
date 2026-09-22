-- id: smoke/aws_regions
-- scenario: smoke
-- providers: aws
-- params: aws_region
-- expected_columns: region_name, endpoint
-- description: enabled AWS regions - one cheap call that proves aws auth
SELECT region_name, endpoint
FROM aws.ec2.regions
WHERE region = '{{ aws_region }}'
ORDER BY region_name
