-- id: finops/remediation/aws_release_address
-- kind: mutation
-- providers: aws
-- params: aws_region, allocation_id
-- description: release one unassociated Elastic IP (aws.ec2.address release_address is an EXEC method)
EXEC aws.ec2.address.release_address
  @region = '{{ aws_region }}',
  @AllocationId = '{{ allocation_id }}'
