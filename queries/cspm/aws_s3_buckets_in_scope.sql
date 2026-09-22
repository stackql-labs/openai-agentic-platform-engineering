-- id: cspm/aws_s3_buckets_in_scope
-- scenario: cspm
-- providers: aws
-- params: aws_region, s3_bucket_prefix
-- expected_columns: bucket, creation_date
-- description: inventory of buckets in scope for per-bucket checks (the name prefix scopes the fan-out; a full sweep pages the whole list in batches)
SELECT name AS bucket, creation_date
FROM aws.s3.buckets
WHERE region = '{{ aws_region }}'
  AND name LIKE '{{ s3_bucket_prefix }}%'
ORDER BY name
