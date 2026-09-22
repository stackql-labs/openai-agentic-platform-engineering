-- id: cspm/google_buckets_public_access_prevention
-- scenario: cspm
-- providers: google
-- params: google_project
-- expected_columns: bucket, location, public_access_prevention, uniform_bucket_level_access, labels
-- description: buckets not enforcing public access prevention (candidates for a public IAM grant); one call per project
WITH b AS (
  SELECT name, location, iamConfiguration AS iam_cfg, labels
  FROM google.storage.buckets
  WHERE project = '{{ google_project }}'
)
SELECT name AS bucket, location,
  json_extract(iam_cfg, '$.publicAccessPrevention') AS public_access_prevention,
  json_extract(iam_cfg, '$.uniformBucketLevelAccess.enabled') AS uniform_bucket_level_access,
  labels
FROM b
WHERE COALESCE(json_extract(iam_cfg, '$.publicAccessPrevention'), 'inherited') <> 'enforced'
