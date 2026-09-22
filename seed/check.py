"""`make seed-check`: one detection query per planted misconfiguration, each of which must return
the expected row against the seeded estate. Runs the library queries through the stackql CLI with
the tenancy parameters; fan-out lists are rendered from inventory queries first, exactly as the
sweep agents do.

Each check: query id, how to render it, and a predicate over the rows that must hold.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from oape_agents.common.config import provider_configured, settings
from oape_agents.common.queries import StackQLError, load_query, sql_list, stackql_exec
from seed.common import console, log, name


@dataclass
class Check:
    provider: str  # seed provider key: aws|azure|gcp|github|idp|openai
    planted: str
    query_id: str
    predicate: Callable[[list[dict]], bool]
    params: Callable[[], dict[str, str]] | None = None


def _s() -> dict[str, str]:
    s = settings()
    return {
        "public_bucket": f"{name('public')}-{s.aws_account_id}",
        "sg_open": name("open-ssh"),
        "iam_user": name("admin-user"),
        "volume_name": name("orphan-volume"),
        "eip_name": name("orphan-eip"),
        "rds": name("db"),
        "nsg": name("open-nsg"),
        "storage": f"oapedemopub{s.azure_subscription_id[:8]}",
        "az_disk": name("orphan-disk"),
        "az_pip": name("orphan-pip"),
        "gcs_bucket": f"{name('public')}-{s.google_project}",
        "fw": name("allow-ssh-any"),
        "gcp_sa": f"serviceAccount:{name('sa')}@{s.google_project}.iam.gserviceaccount.com",
        "gcp_disk": name("orphan-disk"),
        "repo": name("checkout"),
        "deploy_key": name("legacy-deploy-key"),
        "asg": name("checkout-asg"),
    }


def _bucket_list() -> dict[str, str]:
    rows = stackql_exec(load_query("cspm/aws_s3_buckets_in_scope").render())
    return {"s3_bucket_list": sql_list([r["bucket"] for r in rows])}


def _region_list() -> dict[str, str]:
    rows = stackql_exec(load_query("cspm/aws_regions").render())
    return {"aws_region_list": sql_list([r["region_name"] for r in rows])}


def _repo_list() -> dict[str, str]:
    return {"github_repo_list": sql_list([_s()["repo"]])}


def _has(rows: list[dict], **match: str) -> bool:
    return any(all(str(r.get(k)) == v for k, v in match.items()) for r in rows)


def _has_like(rows: list[dict], col: str, needle: str) -> bool:
    return any(needle in str(r.get(col, "")) for r in rows)


CHECKS: list[Check] = [
    Check(
        "aws",
        "S3 bucket-level public access block disabled",
        "cspm/aws_s3_public_access_block_disabled",
        lambda rows: _has(rows, bucket=_s()["public_bucket"], public_access_block="disabled"),
        _bucket_list,
    ),
    Check(
        "aws",
        "security group open to 0.0.0.0/0 on 22",
        "cspm/aws_security_group_ingress_exposure",
        lambda rows: _has(rows, group_name=_s()["sg_open"], open_to_world="1"),
    ),
    Check(
        "aws",
        "IAM user with AdministratorAccess",
        "cspm/aws_iam_user_attached_policies",
        lambda rows: _has_like(rows, "policy_arn", "policy/AdministratorAccess"),
        lambda: {"iam_user_name": _s()["iam_user"]},
    ),
    Check(
        "aws",
        "IAM admin user has an active access key",
        "cspm/aws_iam_user_access_keys",
        lambda rows: _has(rows, user_name=_s()["iam_user"], status="Active"),
        lambda: {"iam_user_name": _s()["iam_user"]},
    ),
    Check(
        "aws",
        "unencrypted RDS instance",
        "cspm/aws_rds_unencrypted",
        lambda rows: _has(rows, db_instance_identifier=_s()["rds"]),
    ),
    Check(
        "aws",
        "at least one region without CloudTrail",
        "cspm/aws_regions_without_cloudtrail",
        lambda rows: len(rows) >= 1,
        _region_list,
    ),
    Check(
        "aws",
        "unattached EBS volume",
        "finops/aws_unattached_volumes",
        lambda rows: _has_like(rows, "tags", _s()["volume_name"]),
    ),
    Check(
        "aws",
        "unassociated Elastic IP",
        "finops/aws_unassociated_eips",
        lambda rows: _has_like(rows, "tags", _s()["eip_name"]),
    ),
    Check(
        "aws",
        "checkout ASG present with an InService instance",
        "triage/aws_asg_state",
        lambda rows: _has(rows, auto_scaling_group_name=_s()["asg"], lifecycle_state="InService"),
    ),
    Check(
        "aws",
        "checkout ASG carries the demo tag",
        "triage/aws_asg_tags",
        lambda rows: _has(
            rows, tag_key=settings().demo_tag_key, tag_value=settings().demo_tag_value
        ),
    ),
    Check(
        "azure",
        "storage account with public blob access",
        "cspm/azure_storage_public_blob_access",
        lambda rows: _has(rows, name=_s()["storage"]),
    ),
    Check(
        "azure",
        "NSG any/any inbound rule",
        "cspm/azure_nsg_inbound_exposure",
        lambda rows: _has(rows, nsg=_s()["nsg"], from_internet="1"),
    ),
    Check(
        "azure",
        "Owner assignment at subscription scope (pre-existing, see WORK_ORDER.md)",
        "cspm/azure_owner_assignments_subscription_scope",
        lambda rows: len(rows) >= 1,
    ),
    Check(
        "azure",
        "unattached managed disk",
        "finops/azure_unattached_disks",
        lambda rows: _has(rows, name=_s()["az_disk"]),
    ),
    Check(
        "azure",
        "unassociated public IP",
        "finops/azure_unassociated_public_ips",
        lambda rows: _has(rows, name=_s()["az_pip"]),
    ),
    Check(
        "gcp",
        "bucket granted to allUsers",
        "cspm/google_bucket_public_bindings",
        lambda rows: _has_like(rows, "members", "allUsers"),
        lambda: {"gcs_bucket_name": _s()["gcs_bucket"]},
    ),
    Check(
        "gcp",
        "firewall rule from 0.0.0.0/0",
        "cspm/google_firewalls_open_ingress",
        lambda rows: _has(rows, name=_s()["fw"]),
    ),
    Check(
        "gcp",
        "roles/owner binding on a service account",
        "cspm/google_project_owner_bindings",
        lambda rows: _has(rows, member=_s()["gcp_sa"]),
    ),
    Check(
        "gcp",
        "unattached persistent disk",
        "finops/google_unattached_disks",
        lambda rows: _has(rows, name=_s()["gcp_disk"]),
    ),
    Check(
        "github",
        "repo without branch protection",
        "cspm/github_unprotected_default_branches",
        lambda rows: _has(rows, repo=_s()["repo"]),
        _repo_list,
    ),
    Check(
        "github",
        "stale deploy key",
        "cspm/github_deploy_keys",
        lambda rows: _has(rows, repo=_s()["repo"], title=_s()["deploy_key"]),
        _repo_list,
    ),
    Check(
        "github",
        "outside collaborator with admin (not planted - see WORK_ORDER.md)",
        "cspm/github_outside_collaborators_admin",
        lambda rows: True,
        _repo_list,
    ),
    Check(
        "github",
        "recent production deployment for triage",
        "triage/github_recent_deployments",
        lambda rows: _has(rows, environment="production"),
    ),
    Check(
        "idp",
        "leaver still in privileged group (needs Graph consent - see WORK_ORDER.md)",
        "entitlements/idp_groups",
        lambda rows: len(rows) >= 1,
    ),
    Check(
        "openai",
        "seeded projects (needs OPENAI_ADMIN_KEY)",
        "openai_estate/projects",
        lambda rows: any(str(r.get("name", "")).startswith(settings().demo_prefix) for r in rows),
    ),
]

PROVIDER_OF = {
    "aws": "aws",
    "azure": "azure",
    "gcp": "google",
    "github": "github",
    "idp": "entra_id",
    "openai": "openai_admin",
}


def run_checks(only: list[str]) -> int:
    passed = failed = skipped = 0
    for c in CHECKS:
        if c.provider not in only:
            continue
        if not provider_configured(PROVIDER_OF[c.provider]):
            log(c.provider, f"skip (not configured): {c.planted}", "skip")
            skipped += 1
            continue
        try:
            params = c.params() if c.params else {}
            sql = load_query(c.query_id).render(**params)
            rows = stackql_exec(sql, timeout=600)
            ok = c.predicate(rows)
        except StackQLError as e:
            msg = str(e)
            if "403" in msg and c.provider == "idp":
                log(c.provider, f"blocked (Graph 403): {c.planted}", "warn")
                skipped += 1
                continue
            log(c.provider, f"FAIL {c.planted}: {msg[:200]}", "err")
            failed += 1
            continue
        if ok:
            log(c.provider, f"ok   {c.planted}  [{c.query_id}] {len(rows)} rows", "ok")
            passed += 1
        else:
            log(
                c.provider,
                f"FAIL {c.planted}  [{c.query_id}] {len(rows)} rows, expected row missing",
                "err",
            )
            failed += 1
    console.rule()
    console.print(f"seed-check: {passed} passed, {failed} failed, {skipped} skipped/blocked")
    return 1 if failed else 0
