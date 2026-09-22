-- id: cspm/google_bucket_public_bindings
-- scenario: cspm
-- providers: google
-- params: gcs_bucket_name
-- expected_columns: bucket, role, members
-- description: IAM bindings on one bucket that grant allUsers or allAuthenticatedUsers; render_union over candidate buckets
WITH p AS (
  SELECT '{{ gcs_bucket_name }}' AS bucket, role, members
  FROM google.storage.buckets_iam_policies
  WHERE bucket = '{{ gcs_bucket_name }}'
)
SELECT bucket, role, members
FROM p
WHERE members LIKE '%allUsers%' OR members LIKE '%allAuthenticatedUsers%'
