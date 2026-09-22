"""Perturb the terraform-managed subset out of band (make tf-perturb), so live state diverges from
tfstate in three known ways the drift scenario must find and classify:

  benign   : instance tag env prod -> staging               (tag churn)
  material : security group gains tcp/8080 from 0.0.0.0/0    (security-relevant)

`--restore` puts both back (the same as `terraform apply` would). Every target is checked
for the demo tag before it is touched.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import boto3

from oape_agents.common.config import settings
from seed.common import log, tag_key, tag_value

TF_DIR = Path(__file__).resolve().parent


def outputs() -> dict:
    out = subprocess.run(["terraform", "output", "-json"], cwd=TF_DIR, capture_output=True, text=True, check=True).stdout
    return {k: v["value"] for k, v in json.loads(out).items()}


def _assert_tag(tags: list[dict], what: str) -> None:
    if not any(t.get("Key") == tag_key() and t.get("Value") == tag_value() for t in tags):
        raise RuntimeError(f"{what} lacks {tag_key()}={tag_value()}: refusing to touch it")


def perturb(restore: bool = False) -> None:
    o = outputs()
    s = settings()
    ec2 = boto3.client("ec2", region_name=s.aws_region)
    inst = ec2.describe_instances(InstanceIds=[o["instance_id"]])["Reservations"][0]["Instances"][0]
    _assert_tag(inst.get("Tags", []), o["instance_id"])
    ec2.create_tags(Resources=[o["instance_id"]], Tags=[{"Key": "env", "Value": "prod" if restore else "staging"}])
    log("perturb", f"{o['instance_id']} tag env -> {'prod' if restore else 'staging'}", "ok")

    sg = ec2.describe_security_groups(GroupIds=[o["security_group_id"]])["SecurityGroups"][0]
    _assert_tag(sg.get("Tags", []), o["security_group_id"])
    perm = [{"IpProtocol": "tcp", "FromPort": 8080, "ToPort": 8080, "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "out-of-band change"}]}]
    try:
        if restore:
            ec2.revoke_security_group_ingress(GroupId=o["security_group_id"], IpPermissions=perm)
        else:
            ec2.authorize_security_group_ingress(GroupId=o["security_group_id"], IpPermissions=perm)
    except ec2.exceptions.ClientError as e:
        if e.response["Error"]["Code"] not in ("InvalidPermission.Duplicate", "InvalidPermission.NotFound"):
            raise
    log("perturb", f"{o['security_group_id']} tcp/8080 from 0.0.0.0/0 {'revoked' if restore else 'added'}", "ok")



if __name__ == "__main__":
    perturb(restore="--restore" in sys.argv)
