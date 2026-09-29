-- id: entitlements/aws_admin_user_literal
-- providers: aws
-- params: user_name
-- expected_columns: user_name
-- description: one literal row; render_query renders one per AWS user that holds AdministratorAccess and UNION ALL-s them into the aws_admins CTE body of entitlements/privileged_principals_all_clouds (StackQL cannot json_each a literal)
SELECT '{{ user_name }}' AS user_name
