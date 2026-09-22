-- id: cspm/aws_iam_user_attached_policies
-- scenario: cspm
-- providers: aws
-- params: aws_iam_region, iam_user_name
-- expected_columns: user_name, policy_name, policy_arn
-- description: managed policies attached to one user; render_union over user names keeps rows attributed (the API does not echo the user)
SELECT '{{ iam_user_name }}' AS user_name, policy_name, policy_arn
FROM aws.iam.attached_user_policies
WHERE region = '{{ aws_iam_region }}'
  AND user_name = '{{ iam_user_name }}'
