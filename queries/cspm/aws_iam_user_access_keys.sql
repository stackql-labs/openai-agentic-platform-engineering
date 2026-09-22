-- id: cspm/aws_iam_user_access_keys
-- scenario: cspm
-- providers: aws
-- params: aws_iam_region, iam_user_name
-- expected_columns: user_name, access_key_id, status, create_date, age_days
-- description: access keys for one user with real age in days; render_union over user names
SELECT '{{ iam_user_name }}' AS user_name, access_key_id, status, create_date,
  ROUND(julianday('now') - julianday(create_date), 0) AS age_days
FROM aws.iam.access_keys
WHERE region = '{{ aws_iam_region }}'
  AND user_name = '{{ iam_user_name }}'
