-- id: cspm/aws_iam_users
-- scenario: cspm
-- providers: aws
-- params: aws_iam_region
-- expected_columns: user_name, user_id, arn, create_date, password_last_used, tags
-- description: IAM users in the account (IAM is global; the provider signs against us-east-1)
SELECT user_name, user_id, arn, create_date, password_last_used, tags
FROM aws.iam.users
WHERE region = '{{ aws_iam_region }}'
ORDER BY user_name
