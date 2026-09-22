"""AWS seed (boto3). Plants, all tagged purpose=oape-demo in the demo account and region:

  cspm    : S3 bucket whose bucket-level public access block is fully disabled (see note),
            security group open to 0.0.0.0/0 on tcp/22, IAM user with AdministratorAccess and an
            active access key (secret discarded at creation, never stored), unencrypted RDS
            instance (smallest class), CloudTrail trail in the primary region only (every other
            region is uncovered)
  finops  : unattached EBS volume, unassociated Elastic IP
  triage  : the "checkout" service - launch template + ASG (min 1, desired 1, max 3) behind an
            internal ALB with a target group, so instance/ASG state and target health are
            queryable and "scale out by one" is a small, reversible mutation

Substitution (recorded in WORK_ORDER.md): the demo account has account-level S3 Block Public
Access enabled on all four settings, so a public bucket policy or ACL cannot be applied. The
planted finding is therefore "bucket-level public access block disabled"; the reasoning
tier is expected to correlate it with the account-level setting when judging materiality.

Teardown discovers resources by tag through the Resource Groups Tagging API plus name lookups
for resources the tagging API does not index, verifies the tag on every target, and deletes in
dependency order. It is idempotent: a second run finds nothing.
"""

from __future__ import annotations

import base64
import json
import secrets
import time

import boto3
from botocore.exceptions import ClientError

from oape_agents.common.config import settings
from seed.common import SeedReport, log, name, save_state, tag_key, tag_value, wait_for

MANAGED_BY = "oape-seed"


def _tags(extra: dict | None = None) -> list[dict]:
    t = {tag_key(): tag_value(), "managed-by": MANAGED_BY}
    if extra:
        t.update(extra)
    return [{"Key": k, "Value": v} for k, v in t.items()]


def _is_demo(tags: list[dict] | None) -> bool:
    return any(t.get("Key") == tag_key() and t.get("Value") == tag_value() for t in (tags or []))


class Aws:
    def __init__(self) -> None:
        s = settings()
        self.region = s.aws_region
        self.account = s.aws_account_id
        self.sess = boto3.session.Session(region_name=self.region)
        self.ec2 = self.sess.client("ec2")
        self.s3 = self.sess.client("s3")
        self.iam = self.sess.client("iam")
        self.rds = self.sess.client("rds")
        self.ct = self.sess.client("cloudtrail")
        self.asg = self.sess.client("autoscaling")
        self.elb = self.sess.client("elbv2")
        self.ssm = self.sess.client("ssm")
        self.tagging = self.sess.client("resourcegroupstaggingapi")
        self.sts = self.sess.client("sts")
        ident = self.sts.get_caller_identity()
        if self.account and ident["Account"] != self.account:
            raise RuntimeError(
                f"credentials are for account {ident['Account']} but AWS_ACCOUNT_ID={self.account}: refusing"
            )
        self.account = ident["Account"]

    # --- names -------------------------------------------------------------------------
    @property
    def n(self) -> dict[str, str]:
        return {
            "public_bucket": f"{name('public')}-{self.account}",
            "trail_bucket": f"{name('cloudtrail')}-{self.account}",
            "trail": name("trail"),
            "sg_open": name("open-ssh"),
            "iam_user": name("admin-user"),
            "volume": name("orphan-volume"),
            "eip": name("orphan-eip"),
            "rds": name("db"),
            "lt": name("checkout-lt"),
            "asg": name("checkout-asg"),
            "alb": name("checkout-alb"),
            "tg": name("checkout-tg"),
            "sg_web": name("checkout-web-sg"),
            "sg_alb": name("checkout-alb-sg"),
        }

    def default_vpc(self) -> tuple[str, list[str]]:
        vpcs = self.ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
        if not vpcs:
            raise RuntimeError("no default VPC in the demo region")
        vpc = vpcs[0]["VpcId"]
        subnets = self.ec2.describe_subnets(
            Filters=[
                {"Name": "vpc-id", "Values": [vpc]},
                {"Name": "default-for-az", "Values": ["true"]},
            ]
        )["Subnets"]
        subnets.sort(key=lambda x: x["AvailabilityZone"])
        return vpc, [x["SubnetId"] for x in subnets]

    # --- seed ---------------------------------------------------------------------------
    def seed(self, rep: SeedReport) -> None:
        st: dict = {"account": self.account, "region": self.region}
        st["public_bucket"] = self.seed_public_bucket(rep)
        st["sg_open"] = self.seed_open_sg(rep)
        st["iam_user"] = self.seed_iam_admin(rep)
        st["volume"] = self.seed_volume(rep)
        st["eip"] = self.seed_eip(rep)
        st["rds"] = self.seed_rds(rep)
        st["trail"] = self.seed_cloudtrail(rep)
        st.update(self.seed_checkout(rep))
        save_state("aws", st)

    def seed_public_bucket(self, rep: SeedReport) -> str:
        b = self.n["public_bucket"]
        if self._bucket_exists(b):
            rep.existed.append(b)
        else:
            self.s3.create_bucket(
                Bucket=b, CreateBucketConfiguration={"LocationConstraint": self.region}
            )
            self.s3.put_bucket_tagging(Bucket=b, Tagging={"TagSet": _tags({"Name": b})})
            rep.created.append(b)
            log("s3", f"created {b}", "ok")
        # the planted misconfiguration: bucket-level public access block present but fully disabled
        # (an explicit all-false configuration is queryable; an absent one returns 404 per bucket)
        self.s3.put_public_access_block(
            Bucket=b,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": False,
                "IgnorePublicAcls": False,
                "BlockPublicPolicy": False,
                "RestrictPublicBuckets": False,
            },
        )
        # attempt a public-read policy; account-level BPA is expected to refuse it
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "PublicRead",
                    "Effect": "Allow",
                    "Principal": "*",
                    "Action": "s3:GetObject",
                    "Resource": f"arn:aws:s3:::{b}/*",
                }
            ],
        }
        try:
            self.s3.put_bucket_policy(Bucket=b, Policy=json.dumps(policy))
            log("s3", f"{b}: public-read bucket policy applied", "warn")
        except ClientError as e:
            rep.substitutions.append(
                f"s3: public bucket policy refused ({e.response['Error']['Code']}) - account-level Block Public "
                "Access is on; planted finding is 'bucket-level public access block disabled' instead"
            )
        return b

    def seed_open_sg(self, rep: SeedReport) -> str:
        vpc, _ = self.default_vpc()
        sg = self._find_sg(self.n["sg_open"], vpc)
        if sg:
            rep.existed.append(self.n["sg_open"])
        else:
            sg = self.ec2.create_security_group(
                GroupName=self.n["sg_open"],
                Description="oape demo: ssh open to the world (planted)",
                VpcId=vpc,
                TagSpecifications=[
                    {"ResourceType": "security-group", "Tags": _tags({"Name": self.n["sg_open"]})}
                ],
            )["GroupId"]
            rep.created.append(self.n["sg_open"])
            log("ec2", f"created sg {sg}", "ok")
        try:
            self.ec2.authorize_security_group_ingress(
                GroupId=sg,
                IpPermissions=[
                    {
                        "IpProtocol": "tcp",
                        "FromPort": 22,
                        "ToPort": 22,
                        "IpRanges": [
                            {"CidrIp": "0.0.0.0/0", "Description": "planted: ssh from anywhere"}
                        ],
                    }
                ],
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
                raise
        return sg

    def seed_iam_admin(self, rep: SeedReport) -> str:
        u = self.n["iam_user"]
        try:
            self.iam.get_user(UserName=u)
            rep.existed.append(u)
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity":
                raise
            self.iam.create_user(UserName=u, Tags=_tags({"Name": u}))
            rep.created.append(u)
            log("iam", f"created user {u}", "ok")
        self.iam.attach_user_policy(
            UserName=u, PolicyArn="arn:aws:iam::aws:policy/AdministratorAccess"
        )
        keys = self.iam.list_access_keys(UserName=u)["AccessKeyMetadata"]
        if not keys:
            k = self.iam.create_access_key(UserName=u)["AccessKey"]
            # the secret is discarded here on purpose: the key exists (the finding) but nobody holds it
            del k
            log("iam", f"created access key for {u} (secret discarded, not stored anywhere)", "ok")
        return u

    def seed_volume(self, rep: SeedReport) -> str:
        found = self._find_volume()
        if found:
            rep.existed.append(self.n["volume"])
            return found
        _, subnets = self.default_vpc()
        az = self.ec2.describe_subnets(SubnetIds=[subnets[0]])["Subnets"][0]["AvailabilityZone"]
        v = self.ec2.create_volume(
            AvailabilityZone=az,
            Size=8,
            VolumeType="gp3",
            Encrypted=False,
            TagSpecifications=[
                {"ResourceType": "volume", "Tags": _tags({"Name": self.n["volume"]})}
            ],
        )["VolumeId"]
        rep.created.append(self.n["volume"])
        log("ec2", f"created unattached volume {v} (8 GiB gp3, unencrypted)", "ok")
        return v

    def seed_eip(self, rep: SeedReport) -> str:
        found = self._find_eip()
        if found:
            rep.existed.append(self.n["eip"])
            return found
        a = self.ec2.allocate_address(
            Domain="vpc",
            TagSpecifications=[
                {"ResourceType": "elastic-ip", "Tags": _tags({"Name": self.n["eip"]})}
            ],
        )
        rep.created.append(self.n["eip"])
        log("ec2", f"allocated unassociated eip {a['PublicIp']}", "ok")
        return a["AllocationId"]

    def seed_rds(self, rep: SeedReport) -> str:
        ident = self.n["rds"]
        try:
            db = self.rds.describe_db_instances(DBInstanceIdentifier=ident)["DBInstances"][0]
            rep.existed.append(ident)
            return db["DBInstanceArn"]
        except ClientError as e:
            if e.response["Error"]["Code"] != "DBInstanceNotFound":
                raise
        pw = secrets.token_urlsafe(24)  # never stored: the instance is a fixture, nobody logs in
        db = self.rds.create_db_instance(
            DBInstanceIdentifier=ident,
            DBInstanceClass="db.t4g.micro",
            Engine="postgres",
            AllocatedStorage=20,
            StorageType="gp2",
            StorageEncrypted=False,
            PubliclyAccessible=False,
            MasterUsername="oapedemo",
            MasterUserPassword=pw,
            BackupRetentionPeriod=0,
            DeletionProtection=False,
            MultiAZ=False,
            AutoMinorVersionUpgrade=True,
            CopyTagsToSnapshot=False,
            Tags=_tags({"Name": ident}),
        )["DBInstance"]
        del pw
        rep.created.append(ident)
        log(
            "rds",
            f"creating {ident} (db.t4g.micro postgres, storage unencrypted) - takes several minutes",
            "ok",
        )
        return db["DBInstanceArn"]

    def seed_cloudtrail(self, rep: SeedReport) -> str:
        b = self.n["trail_bucket"]
        if not self._bucket_exists(b):
            self.s3.create_bucket(
                Bucket=b, CreateBucketConfiguration={"LocationConstraint": self.region}
            )
            self.s3.put_bucket_tagging(Bucket=b, Tagging={"TagSet": _tags({"Name": b})})
            rep.created.append(b)
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "AclCheck",
                    "Effect": "Allow",
                    "Principal": {"Service": "cloudtrail.amazonaws.com"},
                    "Action": "s3:GetBucketAcl",
                    "Resource": f"arn:aws:s3:::{b}",
                },
                {
                    "Sid": "Write",
                    "Effect": "Allow",
                    "Principal": {"Service": "cloudtrail.amazonaws.com"},
                    "Action": "s3:PutObject",
                    "Resource": f"arn:aws:s3:::{b}/AWSLogs/{self.account}/*",
                    "Condition": {"StringEquals": {"s3:x-amz-acl": "bucket-owner-full-control"}},
                },
            ],
        }
        self.s3.put_bucket_policy(Bucket=b, Policy=json.dumps(policy))
        t = self.n["trail"]
        trails = self.ct.describe_trails(trailNameList=[t])["trailList"]
        if trails:
            rep.existed.append(t)
            arn = trails[0]["TrailARN"]
        else:
            arn = self.ct.create_trail(
                Name=t,
                S3BucketName=b,
                IsMultiRegionTrail=False,
                IncludeGlobalServiceEvents=False,
                EnableLogFileValidation=False,
                TagsList=_tags({"Name": t}),
            )["TrailARN"]
            rep.created.append(t)
            log(
                "cloudtrail",
                f"created single-region trail {t} in {self.region}; other regions uncovered",
                "ok",
            )
        self.ct.start_logging(Name=t)
        return arn

    def seed_checkout(self, rep: SeedReport) -> dict:
        vpc, subnets = self.default_vpc()
        out: dict = {}
        sg_alb = (
            self._find_sg(self.n["sg_alb"], vpc)
            or self.ec2.create_security_group(
                GroupName=self.n["sg_alb"],
                Description="oape demo checkout alb",
                VpcId=vpc,
                TagSpecifications=[
                    {
                        "ResourceType": "security-group",
                        "Tags": _tags({"Name": self.n["sg_alb"], "service": "oape-checkout"}),
                    }
                ],
            )["GroupId"]
        )
        sg_web = (
            self._find_sg(self.n["sg_web"], vpc)
            or self.ec2.create_security_group(
                GroupName=self.n["sg_web"],
                Description="oape demo checkout web tier",
                VpcId=vpc,
                TagSpecifications=[
                    {
                        "ResourceType": "security-group",
                        "Tags": _tags({"Name": self.n["sg_web"], "service": "oape-checkout"}),
                    }
                ],
            )["GroupId"]
        )
        vpc_cidr = self.ec2.describe_vpcs(VpcIds=[vpc])["Vpcs"][0]["CidrBlock"]
        for gid, perms in (
            (
                sg_alb,
                [
                    {
                        "IpProtocol": "tcp",
                        "FromPort": 80,
                        "ToPort": 80,
                        "IpRanges": [{"CidrIp": vpc_cidr}],
                    }
                ],
            ),
            (
                sg_web,
                [
                    {
                        "IpProtocol": "tcp",
                        "FromPort": 80,
                        "ToPort": 80,
                        "UserIdGroupPairs": [{"GroupId": sg_alb}],
                    }
                ],
            ),
        ):
            try:
                self.ec2.authorize_security_group_ingress(GroupId=gid, IpPermissions=perms)
            except ClientError as e:
                if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
                    raise
        out["sg_alb"], out["sg_web"] = sg_alb, sg_web

        # launch template
        ami = self.ssm.get_parameter(
            Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64"
        )["Parameter"]["Value"]
        user_data = base64.b64encode(
            b"#!/bin/bash\nmkdir -p /var/www && cd /var/www && echo '<h1>oape checkout</h1>' > index.html && nohup python3 -m http.server 80 >/dev/null 2>&1 &\n"
        ).decode()
        lts = self.ec2.describe_launch_templates(
            Filters=[{"Name": "launch-template-name", "Values": [self.n["lt"]]}]
        )["LaunchTemplates"]
        if lts:
            lt_id = lts[0]["LaunchTemplateId"]
            rep.existed.append(self.n["lt"])
        else:
            lt_id = self.ec2.create_launch_template(
                LaunchTemplateName=self.n["lt"],
                LaunchTemplateData={
                    "ImageId": ami,
                    "InstanceType": "t3.micro",
                    "SecurityGroupIds": [sg_web],
                    "UserData": user_data,
                    "TagSpecifications": [
                        {
                            "ResourceType": "instance",
                            "Tags": _tags({"Name": name("checkout"), "service": "oape-checkout"}),
                        },
                        {"ResourceType": "volume", "Tags": _tags({"service": "oape-checkout"})},
                    ],
                    "MetadataOptions": {"HttpTokens": "required"},
                },
                TagSpecifications=[
                    {
                        "ResourceType": "launch-template",
                        "Tags": _tags({"Name": self.n["lt"], "service": "oape-checkout"}),
                    }
                ],
            )["LaunchTemplate"]["LaunchTemplateId"]
            rep.created.append(self.n["lt"])
        out["lt"] = lt_id

        # target group + internal alb + listener
        tgs = self._find_tg()
        if tgs:
            tg_arn = tgs["TargetGroupArn"]
            rep.existed.append(self.n["tg"])
        else:
            tg_arn = self.elb.create_target_group(
                Name=self.n["tg"],
                Protocol="HTTP",
                Port=80,
                VpcId=vpc,
                HealthCheckPath="/",
                HealthCheckIntervalSeconds=10,
                HealthyThresholdCount=2,
                UnhealthyThresholdCount=2,
                Tags=_tags({"Name": self.n["tg"], "service": "oape-checkout"}),
            )["TargetGroups"][0]["TargetGroupArn"]
            rep.created.append(self.n["tg"])
        out["tg"] = tg_arn
        albs = self._find_alb()
        if albs:
            alb_arn = albs["LoadBalancerArn"]
            rep.existed.append(self.n["alb"])
        else:
            alb_arn = self.elb.create_load_balancer(
                Name=self.n["alb"],
                Subnets=subnets[:2],
                SecurityGroups=[sg_alb],
                Scheme="internal",
                Type="application",
                Tags=_tags({"Name": self.n["alb"], "service": "oape-checkout"}),
            )["LoadBalancers"][0]["LoadBalancerArn"]
            rep.created.append(self.n["alb"])
            log("elbv2", f"created internal alb {self.n['alb']}", "ok")
        out["alb"] = alb_arn
        listeners = self.elb.describe_listeners(LoadBalancerArn=alb_arn)["Listeners"]
        if not listeners:
            self.elb.create_listener(
                LoadBalancerArn=alb_arn,
                Protocol="HTTP",
                Port=80,
                DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}],
            )

        # asg
        try:
            self.asg.describe_auto_scaling_groups(AutoScalingGroupNames=[self.n["asg"]])[
                "AutoScalingGroups"
            ][0]
            rep.existed.append(self.n["asg"])
        except IndexError:
            self.asg.create_auto_scaling_group(
                AutoScalingGroupName=self.n["asg"],
                LaunchTemplate={"LaunchTemplateId": lt_id, "Version": "$Latest"},
                MinSize=1,
                MaxSize=3,
                DesiredCapacity=1,
                VPCZoneIdentifier=",".join(subnets[:2]),
                TargetGroupARNs=[tg_arn],
                HealthCheckType="EC2",
                HealthCheckGracePeriod=120,
                Tags=[
                    {
                        "Key": t["Key"],
                        "Value": t["Value"],
                        "PropagateAtLaunch": True,
                        "ResourceType": "auto-scaling-group",
                        "ResourceId": self.n["asg"],
                    }
                    for t in _tags({"Name": name("checkout"), "service": "oape-checkout"})
                ],
            )
            rep.created.append(self.n["asg"])
            log("autoscaling", f"created asg {self.n['asg']} min=1 desired=1 max=3", "ok")
        out["asg"] = self.n["asg"]
        return out

    # --- teardown ----------------------------------------------------------------------
    def teardown(self, rep: SeedReport) -> None:
        self._teardown_checkout(rep)
        self._teardown_rds(rep)
        self._teardown_trail(rep)
        self._teardown_eip(rep)
        self._teardown_volume(rep)
        self._teardown_sg(self.n["sg_open"], rep)
        self._teardown_iam(rep)
        for b in (self.n["public_bucket"], self.n["trail_bucket"]):
            self._teardown_bucket(b, rep)
        self._rds_wait_gone(rep)
        leftovers = self.tagged_arns()
        if leftovers:
            log("teardown", f"still tagged after teardown: {leftovers}", "warn")
        else:
            log("teardown", "no resources carry the demo tag", "ok")

    def tagged_arns(self) -> list[str]:
        out = []
        pag = self.tagging.get_paginator("get_resources")
        for page in pag.paginate(TagFilters=[{"Key": tag_key(), "Values": [tag_value()]}]):
            out += [r["ResourceARN"] for r in page["ResourceTagMappingList"]]
        return out

    def _teardown_checkout(self, rep: SeedReport) -> None:
        try:
            g = self.asg.describe_auto_scaling_groups(AutoScalingGroupNames=[self.n["asg"]])[
                "AutoScalingGroups"
            ][0]
            if _is_demo(g.get("Tags")):
                self.asg.delete_auto_scaling_group(
                    AutoScalingGroupName=self.n["asg"], ForceDelete=True
                )
                wait_for(
                    "asg delete",
                    lambda: (
                        not self.asg.describe_auto_scaling_groups(
                            AutoScalingGroupNames=[self.n["asg"]]
                        )["AutoScalingGroups"]
                    ),
                    timeout=600,
                    interval=15,
                )
                rep.deleted.append(self.n["asg"])
        except IndexError:
            pass
        alb = self._find_alb()
        if alb:
            for lsn in self.elb.describe_listeners(LoadBalancerArn=alb["LoadBalancerArn"])[
                "Listeners"
            ]:
                self.elb.delete_listener(ListenerArn=lsn["ListenerArn"])
            self.elb.delete_load_balancer(LoadBalancerArn=alb["LoadBalancerArn"])
            wait_for("alb delete", lambda: self._find_alb() is None, timeout=300, interval=10)
            rep.deleted.append(self.n["alb"])
        tg = self._find_tg()
        if tg:
            self.elb.delete_target_group(TargetGroupArn=tg["TargetGroupArn"])
            rep.deleted.append(self.n["tg"])
        for lt in self.ec2.describe_launch_templates(
            Filters=[{"Name": "launch-template-name", "Values": [self.n["lt"]]}]
        )["LaunchTemplates"]:
            if _is_demo(lt.get("Tags")):
                self.ec2.delete_launch_template(LaunchTemplateId=lt["LaunchTemplateId"])
                rep.deleted.append(self.n["lt"])
        # instances launched by the asg are terminated by ForceDelete; wait for them so SGs can go
        wait_for(
            "checkout instances terminate",
            lambda: not self._running_demo_instances("oape-checkout"),
            timeout=600,
            interval=15,
        )
        self._teardown_sg(self.n["sg_web"], rep)
        self._teardown_sg(self.n["sg_alb"], rep)

    def _running_demo_instances(self, service: str) -> list[str]:
        r = self.ec2.describe_instances(
            Filters=[
                {"Name": f"tag:{tag_key()}", "Values": [tag_value()]},
                {"Name": "tag:service", "Values": [service]},
                {
                    "Name": "instance-state-name",
                    "Values": ["pending", "running", "shutting-down", "stopping", "stopped"],
                },
            ]
        )
        return [i["InstanceId"] for res in r["Reservations"] for i in res["Instances"]]

    def _teardown_rds(self, rep: SeedReport) -> None:
        try:
            db = self.rds.describe_db_instances(DBInstanceIdentifier=self.n["rds"])["DBInstances"][
                0
            ]
        except ClientError as e:
            if e.response["Error"]["Code"] == "DBInstanceNotFound":
                return
            raise
        if not _is_demo(db.get("TagList")):
            log("rds", f"{self.n['rds']} exists but lacks the demo tag: leaving it", "warn")
            return
        if db["DBInstanceStatus"] != "deleting":
            self.rds.delete_db_instance(
                DBInstanceIdentifier=self.n["rds"],
                SkipFinalSnapshot=True,
                DeleteAutomatedBackups=True,
            )
            rep.deleted.append(self.n["rds"])
            log("rds", f"deleting {self.n['rds']}", "ok")

    def _rds_wait_gone(self, rep: SeedReport) -> None:
        def gone() -> bool:
            try:
                self.rds.describe_db_instances(DBInstanceIdentifier=self.n["rds"])
                return False
            except ClientError as e:
                return e.response["Error"]["Code"] == "DBInstanceNotFound"

        if not gone():
            log("rds", "waiting for instance deletion to complete", "info")
            wait_for("rds delete", gone, timeout=1200, interval=20)

    def _teardown_trail(self, rep: SeedReport) -> None:
        trails = self.ct.describe_trails(trailNameList=[self.n["trail"]])["trailList"]
        for t in trails:
            tags = self.ct.list_tags(ResourceIdList=[t["TrailARN"]])["ResourceTagList"][0].get(
                "TagsList", []
            )
            if _is_demo(tags):
                self.ct.delete_trail(Name=t["TrailARN"])
                rep.deleted.append(self.n["trail"])

    def _teardown_eip(self, rep: SeedReport) -> None:
        for a in self.ec2.describe_addresses(
            Filters=[{"Name": f"tag:{tag_key()}", "Values": [tag_value()]}]
        )["Addresses"]:
            if a.get("AssociationId"):
                self.ec2.disassociate_address(AssociationId=a["AssociationId"])
            self.ec2.release_address(AllocationId=a["AllocationId"])
            rep.deleted.append(f"eip {a['PublicIp']}")

    def _teardown_volume(self, rep: SeedReport) -> None:
        for v in self.ec2.describe_volumes(
            Filters=[
                {"Name": f"tag:{tag_key()}", "Values": [tag_value()]},
                {"Name": "tag:Name", "Values": [self.n["volume"]]},
            ]
        )["Volumes"]:
            if v["State"] == "available":
                self.ec2.delete_volume(VolumeId=v["VolumeId"])
                rep.deleted.append(v["VolumeId"])

    def _teardown_sg(self, sg_name: str, rep: SeedReport) -> None:
        vpc, _ = self.default_vpc()
        sg = self._find_sg(sg_name, vpc)
        if not sg:
            return
        desc = self.ec2.describe_security_groups(GroupIds=[sg])["SecurityGroups"][0]
        if not _is_demo(desc.get("Tags")):
            return

        def try_delete() -> bool:
            try:
                self.ec2.delete_security_group(GroupId=sg)
                return True
            except ClientError as e:
                return e.response["Error"]["Code"] not in ("DependencyViolation",)

        if wait_for(f"sg {sg_name} delete", try_delete, timeout=300, interval=10):
            rep.deleted.append(sg_name)

    def _teardown_iam(self, rep: SeedReport) -> None:
        u = self.n["iam_user"]
        try:
            user = self.iam.get_user(UserName=u)["User"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchEntity":
                return
            raise
        if not _is_demo(user.get("Tags")):
            log("iam", f"{u} exists but lacks the demo tag: leaving it", "warn")
            return
        for k in self.iam.list_access_keys(UserName=u)["AccessKeyMetadata"]:
            self.iam.delete_access_key(UserName=u, AccessKeyId=k["AccessKeyId"])
        for p in self.iam.list_attached_user_policies(UserName=u)["AttachedPolicies"]:
            self.iam.detach_user_policy(UserName=u, PolicyArn=p["PolicyArn"])
        for p in self.iam.list_user_policies(UserName=u)["PolicyNames"]:
            self.iam.delete_user_policy(UserName=u, PolicyName=p)
        self.iam.delete_user(UserName=u)
        rep.deleted.append(u)

    def _teardown_bucket(self, b: str, rep: SeedReport) -> None:
        if not self._bucket_exists(b):
            return
        try:
            tags = self.s3.get_bucket_tagging(Bucket=b)["TagSet"]
        except ClientError:
            tags = []
        if not _is_demo(tags):
            log("s3", f"{b} exists but lacks the demo tag: leaving it", "warn")
            return
        pag = self.s3.get_paginator("list_object_versions")
        for page in pag.paginate(Bucket=b):
            objs = [
                {"Key": o["Key"], "VersionId": o["VersionId"]}
                for o in page.get("Versions", []) + page.get("DeleteMarkers", [])
            ]
            if objs:
                self.s3.delete_objects(Bucket=b, Delete={"Objects": objs, "Quiet": True})
        self.s3.delete_bucket(Bucket=b)
        rep.deleted.append(b)

    # --- lookups ------------------------------------------------------------------------
    def _bucket_exists(self, b: str) -> bool:
        try:
            self.s3.head_bucket(Bucket=b)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchBucket"):
                return False
            if e.response["Error"]["Code"] in ("403",):
                raise RuntimeError(f"bucket {b} exists in another account") from e
            raise

    def _find_sg(self, sg_name: str, vpc: str) -> str | None:
        r = self.ec2.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": [sg_name]},
                {"Name": "vpc-id", "Values": [vpc]},
            ]
        )["SecurityGroups"]
        return r[0]["GroupId"] if r else None

    def _find_volume(self) -> str | None:
        r = self.ec2.describe_volumes(
            Filters=[
                {"Name": "tag:Name", "Values": [self.n["volume"]]},
                {"Name": "status", "Values": ["available", "creating"]},
            ]
        )["Volumes"]
        return r[0]["VolumeId"] if r else None

    def _find_eip(self) -> str | None:
        r = self.ec2.describe_addresses(Filters=[{"Name": "tag:Name", "Values": [self.n["eip"]]}])[
            "Addresses"
        ]
        return r[0]["AllocationId"] if r else None

    def _find_tg(self) -> dict | None:
        try:
            return self.elb.describe_target_groups(Names=[self.n["tg"]])["TargetGroups"][0]
        except ClientError as e:
            if e.response["Error"]["Code"] == "TargetGroupNotFound":
                return None
            raise

    def _find_alb(self) -> dict | None:
        try:
            return self.elb.describe_load_balancers(Names=[self.n["alb"]])["LoadBalancers"][0]
        except ClientError as e:
            if e.response["Error"]["Code"] == "LoadBalancerNotFound":
                return None
            raise

    # --- status --------------------------------------------------------------------------
    def status(self) -> None:
        arns = self.tagged_arns()
        log("aws", f"{len(arns)} resources tagged {tag_key()}={tag_value()} (tagging api view)")
        for a in sorted(arns):
            log("aws", f"  {a}")
        try:
            db = self.rds.describe_db_instances(DBInstanceIdentifier=self.n["rds"])["DBInstances"][
                0
            ]
            log(
                "aws",
                f"  rds {self.n['rds']}: {db['DBInstanceStatus']} encrypted={db['StorageEncrypted']}",
            )
        except ClientError:
            log("aws", "  rds: absent")
        try:
            g = self.asg.describe_auto_scaling_groups(AutoScalingGroupNames=[self.n["asg"]])[
                "AutoScalingGroups"
            ][0]
            inst = [
                (i["InstanceId"], i["LifecycleState"], i["HealthStatus"]) for i in g["Instances"]
            ]
            log("aws", f"  asg {self.n['asg']}: desired={g['DesiredCapacity']} instances={inst}")
        except IndexError:
            log("aws", "  asg: absent")
        tg = self._find_tg()
        if tg:
            th = self.elb.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])[
                "TargetHealthDescriptions"
            ]
            log(
                "aws",
                f"  target health: {[(t['Target']['Id'], t['TargetHealth']['State']) for t in th]}",
            )


def seed(rep: SeedReport) -> None:
    Aws().seed(rep)


def teardown(rep: SeedReport) -> None:
    Aws().teardown(rep)


def status() -> None:
    Aws().status()


def scale_checkout(desired: int) -> None:
    """Used by `make triage-reset` and rehearsals: put the ASG back to a known desired capacity."""
    a = Aws()
    g = a.asg.describe_auto_scaling_groups(AutoScalingGroupNames=[a.n["asg"]])["AutoScalingGroups"][
        0
    ]
    if not _is_demo(g.get("Tags")):
        raise RuntimeError("asg lacks the demo tag: refusing")
    a.asg.set_desired_capacity(
        AutoScalingGroupName=a.n["asg"], DesiredCapacity=desired, HonorCooldown=False
    )
    log("autoscaling", f"{a.n['asg']} desired -> {desired}", "ok")
    time.sleep(1)
