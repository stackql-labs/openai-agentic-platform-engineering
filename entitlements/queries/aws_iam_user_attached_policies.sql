-- id: entitlements/aws_iam_user_attached_policies
-- providers: aws
-- params: aws_iam_region, iam_user_name
-- expected_columns: user_name, policy_name, policy_arn
-- description: managed policies attached to one user; render_query with iam_user_name as a JSON array renders one SELECT per user UNION ALL-ed so rows stay attributed (the API does not echo the user); users with arn:aws:iam::aws:policy/AdministratorAccess are the AWS admins
SELECT '{{ iam_user_name }}' AS user_name, policy_name, policy_arn
FROM aws.iam.attached_user_policies
WHERE region = '{{ aws_iam_region }}'
  AND user_name = '{{ iam_user_name }}'
