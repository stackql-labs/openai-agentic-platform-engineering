-- id: cspm/aws_rds_unencrypted
-- scenario: cspm
-- providers: aws
-- params: aws_region
-- expected_columns: db_instance_identifier, db_instance_arn, engine, db_instance_class, db_instance_status, storage_encrypted, publicly_accessible, tag_list
-- description: RDS instances whose storage is not encrypted at rest
SELECT db_instance_identifier, db_instance_arn, engine, db_instance_class, db_instance_status,
  storage_encrypted, publicly_accessible, tag_list
FROM aws.rds.db_instances
WHERE region = '{{ aws_region }}'
  AND storage_encrypted IN ('false', '0', 0)
