-- id: drift/snapshot_aws_buckets
-- scenario: drift
-- providers: aws
-- params: aws_region, s3_bucket_prefix
-- expected_columns: bucket, creation_date
-- description: snapshot source - the in-scope buckets (existence and creation date); materialized as snap_<ts>_aws_buckets. Bucket versioning is not snapshotted: the provider maps get_bucket_versioning to an opaque line_items column (see WORK_ORDER.md)
SELECT name AS bucket, creation_date
FROM aws.s3.buckets
WHERE region = '{{ aws_region }}'
  AND name LIKE '{{ s3_bucket_prefix }}%'
