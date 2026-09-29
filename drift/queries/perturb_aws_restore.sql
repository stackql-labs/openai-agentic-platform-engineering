-- id: drift/perturb_aws_restore
-- providers: aws
-- params: aws_region, group_id
-- expected_columns: (mutation - revoke_security_group_ingress, EXEC)
-- kind: mutation
-- description: perturb --restore - revoke the tcp/22 from 0.0.0.0/0 ingress rule again; the provider exposes revoke only as an EXEC method, so the gate sends this through run_lifecycle_operation
EXEC aws.ec2.security_groups.revoke_security_group_ingress
@region = '{{ aws_region }}',
@GroupId = '{{ group_id }}',
@IpPermissions = '[{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]'
