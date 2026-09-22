"""CSPM sweep: security posture across AWS, Azure, GCP and GitHub. Read-only, scheduled.

uv run python -m oape_agents.sweeps.cspm [--fallback] [--dry-run] [--record]
"""

from __future__ import annotations

import sys

from oape_agents.sweeps.base import SweepSpec, cli

SPEC = SweepSpec(
    scenario="cspm",
    title="CSPM sweep",
    query_ids=[
        "cspm/aws_s3_buckets_in_scope",
        "cspm/aws_s3_public_access_block_disabled",
        "cspm/aws_security_group_ingress_exposure",
        "cspm/aws_iam_users",
        "cspm/aws_iam_user_attached_policies",
        "cspm/aws_iam_user_access_keys",
        "cspm/aws_rds_unencrypted",
        "cspm/aws_regions",
        "cspm/aws_regions_without_cloudtrail",
        "cspm/aws_unencrypted_volumes_on_internet_facing_instances",
        "cspm/azure_storage_public_blob_access",
        "cspm/azure_nsg_inbound_exposure",
        "cspm/azure_owner_assignments_subscription_scope",
        "cspm/google_buckets_public_access_prevention",
        "cspm/google_bucket_public_bindings",
        "cspm/google_firewalls_open_ingress",
        "cspm/google_project_owner_bindings",
        "cspm/github_repos",
        "cspm/github_unprotected_default_branches",
        "cspm/github_deploy_keys",
        "cspm/github_outside_collaborators_admin",
    ],
    classify_instructions=(
        "critical = data or credentials exposed to the internet or to everyone (bucket granted to "
        "allUsers, storage account allowing public blob access, an IAM user with AdministratorAccess "
        "that has an active access key); high = network or privilege exposure (ingress from 0.0.0.0/0 "
        "on 22/3389/any, NSG inbound from any source, firewall from 0.0.0.0/0, unencrypted RDS "
        "storage, roles/owner or Owner held by a service account or at subscription scope, unencrypted "
        "volumes on internet-facing instances); medium = control gaps (public access block disabled "
        "while account-level block public access may still apply, regions without CloudTrail, "
        "unprotected default branch on an active repo, deploy key older than the threshold); "
        "low/info = the rest. Run cspm/aws_iam_user_attached_policies with aws_admin-style fan-out: "
        "pass every user name from cspm/aws_iam_users as iam_user_name (JSON array); then run "
        "cspm/aws_iam_user_access_keys only for users that hold AdministratorAccess. Run "
        "cspm/google_bucket_public_bindings for the buckets cspm/google_buckets_public_access_prevention "
        "returned (gcs_bucket_name as a JSON array). Run the GitHub per-repo queries only for repos whose "
        "name starts with the demo prefix (github_repo_list) - the whole org is out of scope for the demo "
        "sweep. Do not report the checkout web tier's port 80 rules or internal load balancer SGs as findings."
    ),
    escalate_instructions=(
        "Correlate: an S3 bucket with its public access block disabled is contained if the account-level "
        "Block Public Access is still enforced - say so and grade accordingly (you may check "
        "aws.s3control.public_access_blocks). An admin IAM user with an active key created today is still "
        "critical: age is not a mitigant. Owner assignments at subscription scope held by humans are a "
        "standing-privilege finding, not an incident. Draft remediation as the smallest reversible change."
    ),
    artifact="issues",
    context_notes=[
        "The demo account enforces account-level S3 Block Public Access; the seeded bucket has its "
        "bucket-level block disabled.",
        "Access key and deploy key ages are real; the demo thresholds are set to 0 days so freshly "
        "seeded keys are reported.",
    ],
)

if __name__ == "__main__":
    sys.exit(cli(SPEC))
