-- id: cspm/aws_s3_public_access_block_disabled
-- scenario: cspm
-- providers: aws
-- params: aws_region, s3_bucket_list
-- expected_columns: bucket, block_public_acls, ignore_public_acls, block_public_policy, restrict_public_buckets, public_access_block
-- description: buckets whose bucket-level public access block is not fully enforced (fan-out over s3_bucket_list from cspm/aws_s3_buckets_in_scope)
WITH pab AS (
  SELECT bucket, block_public_acls, ignore_public_acls, block_public_policy, restrict_public_buckets,
    CASE
      WHEN block_public_acls IN ('true', '1', 1) AND ignore_public_acls IN ('true', '1', 1)
       AND block_public_policy IN ('true', '1', 1) AND restrict_public_buckets IN ('true', '1', 1) THEN 'enforced'
      WHEN block_public_acls IN ('false', '0', 0) AND ignore_public_acls IN ('false', '0', 0)
       AND block_public_policy IN ('false', '0', 0) AND restrict_public_buckets IN ('false', '0', 0) THEN 'disabled'
      ELSE 'partial'
    END AS public_access_block
  FROM aws.s3.public_access_blocks
  WHERE region = '{{ aws_region }}'
    AND bucket IN ({{ s3_bucket_list }})
)
SELECT bucket, block_public_acls, ignore_public_acls, block_public_policy, restrict_public_buckets, public_access_block
FROM pab
WHERE public_access_block <> 'enforced'
