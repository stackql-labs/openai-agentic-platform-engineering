-- id: entitlements/aws_admin_user_literal
-- scenario: entitlements
-- providers: aws
-- params: user_name
-- expected_columns: user_name
-- description: one literal row; render_union over the AWS users that hold AdministratorAccess produces the aws_admins CTE body for entitlements/privileged_principals_all_clouds (StackQL cannot json_each a literal)
SELECT '{{ user_name }}' AS user_name
