-- id: drift/perturb_aws_open_ssh
-- providers: aws
-- params: aws_region, group_id
-- expected_columns: (mutation - authorize_security_group_ingress)
-- kind: mutation
-- description: perturb - the out-of-band change: add an ingress rule tcp/22 from 0.0.0.0/0 to the stack security group; executed only by the perturb gate via run_mutation_query on a full_access server
UPDATE aws.ec2.security_groups
SET IpPermissions = '[{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "out-of-band change simulated by drift perturb"}]}]'
WHERE GroupId = '{{ group_id }}'
AND region = '{{ aws_region }}'
