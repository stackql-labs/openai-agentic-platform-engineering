-- id: cspm/aws_regions_without_cloudtrail
-- scenario: cspm
-- providers: aws
-- params: aws_region_list
-- expected_columns: region, trails
-- description: enabled regions with no CloudTrail trail visible (multi-region trails appear in every region, so zero means uncovered); fan-out over aws_region_list from cspm/aws_regions
WITH t AS (
  SELECT region, json_array_length(trail_list) AS trails
  FROM aws.cloudtrail.trails
  WHERE region IN ({{ aws_region_list }})
)
SELECT region, trails FROM t WHERE trails = 0 ORDER BY region
