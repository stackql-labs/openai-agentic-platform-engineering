"""GCP seed - every mutation is a StackQL statement run through the stackql CLI with the demo
service account (GOOGLE_CREDENTIALS), the same interface the agents read with.

Plants in GOOGLE_PROJECT:
  network       <prefix>-vpc                 custom-mode VPC with no subnets (zero blast radius)
  firewall      <prefix>-allow-ssh-any       INGRESS tcp/22 from 0.0.0.0/0 on that VPC (cspm)
  disk          <prefix>-orphan-disk         10 GB pd-standard attached to nothing (finops)
  service acct  <prefix>-sa                  with a project-level roles/owner binding (cspm/entitlements)
  bucket        <prefix>-public-<project>    allUsers granted roles/storage.objectViewer (cspm)

Labels purpose=oape-demo where the resource type supports labels (disk, bucket). Networks,
firewall rules and service accounts have no labels: teardown filters those on the demo name
prefix and a description that carries the tag (recorded in WORK_ORDER.md).

IAM policies are read-modify-write: the current policy is SELECTed, the binding added or
removed, and the whole policy REPLACEd with the etag from the read.
"""

from __future__ import annotations

import json

from oape_agents.common.config import settings
from oape_agents.common.queries import StackQLError, stackql_exec
from seed.common import SeedReport, log, name, required, save_state, tag_value, wait_for

DESC = f"purpose={tag_value()} (planted by oape seed)"


def _n() -> dict[str, str]:
    s = settings()
    return {
        "vpc": name("vpc"),
        "fw": name("allow-ssh-any"),
        "disk": name("orphan-disk"),
        "sa": name("sa"),
        "sa_email": f"{name('sa')}@{s.google_project}.iam.gserviceaccount.com",
        "bucket": f"{name('public')}-{s.google_project}",
    }


def _q(v: str) -> str:
    return v.replace("'", "''")


def _exists(sql: str) -> bool:
    try:
        rows = stackql_exec(sql)
    except StackQLError as e:
        if "404" in str(e) or "not found" in str(e).lower():
            return False
        raise
    return any(r and any(v not in (None, "null", "") for v in r.values()) for r in rows)


# --- resources ---------------------------------------------------------------------------


def seed_network(rep: SeedReport) -> None:
    s, n = settings(), _n()
    if _exists(
        f"SELECT name FROM google.compute.networks WHERE project='{s.google_project}' AND network='{n['vpc']}'"
    ):
        rep.existed.append(n["vpc"])
        return
    stackql_exec(
        "INSERT INTO google.compute.networks(project, data__name, data__autoCreateSubnetworks, data__description) "
        f"SELECT '{s.google_project}', '{n['vpc']}', false, '{DESC}'"
    )
    wait_for(
        "vpc create",
        lambda: _exists(
            f"SELECT name FROM google.compute.networks WHERE project='{s.google_project}' AND network='{n['vpc']}'"
        ),
        timeout=120,
        interval=5,
    )
    rep.created.append(n["vpc"])
    log("gcp", f"created network {n['vpc']}", "ok")


def seed_firewall(rep: SeedReport) -> None:
    s, n = settings(), _n()
    if _exists(
        f"SELECT name FROM google.compute.firewalls WHERE project='{s.google_project}' AND firewall='{n['fw']}'"
    ):
        rep.existed.append(n["fw"])
        return
    stackql_exec(
        "INSERT INTO google.compute.firewalls(project, data__name, data__network, data__direction, data__priority, "
        "data__sourceRanges, data__allowed, data__description) "
        f"SELECT '{s.google_project}', '{n['fw']}', 'global/networks/{n['vpc']}', 'INGRESS', 1000, "
        '\'["0.0.0.0/0"]\', \'[{"IPProtocol": "tcp", "ports": ["22"]}]\', '
        f"'{DESC}'"
    )
    rep.created.append(n["fw"])
    log("gcp", f"created firewall {n['fw']} tcp/22 from 0.0.0.0/0", "ok")


def seed_disk(rep: SeedReport) -> None:
    s, n = settings(), _n()
    if _exists(
        f"SELECT name FROM google.compute.disks WHERE project='{s.google_project}' AND zone='{s.google_zone}' AND disk='{n['disk']}'"
    ):
        rep.existed.append(n["disk"])
        return
    labels = json.dumps({settings().demo_tag_key: tag_value()})
    stackql_exec(
        "INSERT INTO google.compute.disks(project, zone, data__name, data__sizeGb, data__type, data__labels, data__description) "
        f"SELECT '{s.google_project}', '{s.google_zone}', '{n['disk']}', '10', "
        f"'zones/{s.google_zone}/diskTypes/pd-standard', '{labels}', '{DESC}'"
    )
    rep.created.append(n["disk"])
    log("gcp", f"created unattached disk {n['disk']} (10 GB pd-standard)", "ok")


def seed_service_account(rep: SeedReport) -> None:
    s, n = settings(), _n()
    if _exists(
        f"SELECT email FROM google.iam.service_accounts WHERE projectsId='{s.google_project}' AND serviceAccountsId='{n['sa_email']}'"
    ):
        rep.existed.append(n["sa"])
    else:
        body = json.dumps(
            {"displayName": f"{n['sa']} (planted owner binding)", "description": DESC}
        )
        stackql_exec(
            "INSERT INTO google.iam.service_accounts(projectsId, data__accountId, data__serviceAccount) "
            f"SELECT '{s.google_project}', '{n['sa']}', '{body}'"
        )
        rep.created.append(n["sa"])
        log("gcp", f"created service account {n['sa_email']}", "ok")
    _project_binding(add=True, rep=rep)


def _rows_to_bindings(rows: list[dict]) -> list[dict]:
    """StackQL flattens an IAM policy into one row per binding (role, members, condition)."""
    out = []
    for r in rows:
        if not r or r.get("role") in (None, "null", ""):
            continue
        m = r.get("members")
        members = json.loads(m) if isinstance(m, str) else (m or [])
        b = {"role": r["role"], "members": members}
        c = r.get("condition")
        if c not in (None, "null", ""):
            b["condition"] = json.loads(c) if isinstance(c, str) else c
        out.append(b)
    return out


def _project_policy() -> dict:
    s = settings()
    rows = stackql_exec(
        f"SELECT role, members, condition FROM google.cloudresourcemanager.projects_iam_policies WHERE projectsId='{s.google_project}'"
    )
    bindings = _rows_to_bindings(rows)
    if not bindings:
        raise RuntimeError("could not read project IAM policy")
    version = 3 if any("condition" in b for b in bindings) else 1
    return {"bindings": bindings, "version": version}


def _project_binding(add: bool, rep: SeedReport) -> None:
    s, n = settings(), _n()
    member = f"serviceAccount:{n['sa_email']}"
    pol = _project_policy()
    owner = next(
        (b for b in pol["bindings"] if b.get("role") == "roles/owner" and not b.get("condition")),
        None,
    )
    has = owner is not None and member in owner.get("members", [])
    if add and has:
        rep.existed.append(f"roles/owner binding for {n['sa']}")
        return
    if not add and not has:
        return
    if add:
        if owner is None:
            pol["bindings"].append({"role": "roles/owner", "members": [member]})
        else:
            owner["members"].append(member)
    else:
        owner["members"] = [m for m in owner["members"] if m != member]
        if not owner["members"]:
            pol["bindings"] = [b for b in pol["bindings"] if b is not owner]
    policy = json.dumps({"bindings": pol["bindings"], "version": pol["version"]})
    stackql_exec(
        f"REPLACE google.cloudresourcemanager.projects_iam_policies SET data__policy = '{_q(policy)}' "
        f"WHERE projectsId = '{s.google_project}'"
    )
    if add:
        rep.created.append(f"roles/owner binding for {n['sa']}")
        log("gcp", f"bound {member} to roles/owner at project level", "ok")
    else:
        rep.deleted.append(f"roles/owner binding for {n['sa']}")


def seed_bucket(rep: SeedReport) -> None:
    s, n = settings(), _n()
    if _exists(f"SELECT name FROM google.storage.buckets WHERE bucket='{n['bucket']}'"):
        rep.existed.append(n["bucket"])
    else:
        labels = json.dumps({settings().demo_tag_key: tag_value()})
        iam_cfg = json.dumps(
            {"uniformBucketLevelAccess": {"enabled": True}, "publicAccessPrevention": "inherited"}
        )
        stackql_exec(
            "INSERT INTO google.storage.buckets(project, data__name, data__location, data__labels, data__iamConfiguration) "
            f"SELECT '{s.google_project}', '{n['bucket']}', 'US', '{labels}', '{iam_cfg}'"
        )
        rep.created.append(n["bucket"])
        log("gcp", f"created bucket {n['bucket']}", "ok")
    rows = stackql_exec(
        f"SELECT role, members, condition FROM google.storage.buckets_iam_policies WHERE bucket='{n['bucket']}'"
    )
    bindings = _rows_to_bindings(rows)
    if any("allUsers" in x.get("members", []) for x in bindings):
        rep.existed.append("allUsers objectViewer")
        return
    bindings.append({"role": "roles/storage.objectViewer", "members": ["allUsers"]})
    try:
        stackql_exec(
            f"REPLACE google.storage.buckets_iam_policies SET data__bindings = '{_q(json.dumps(bindings))}' "
            f"WHERE bucket = '{n['bucket']}'"
        )
        rep.created.append("allUsers objectViewer")
        log("gcp", f"granted allUsers roles/storage.objectViewer on {n['bucket']}", "ok")
    except StackQLError as e:
        rep.substitutions.append(
            f"gcp: allUsers grant refused ({str(e)[:120]}) - an org policy enforces public access prevention; "
            "planted finding is 'bucket with publicAccessPrevention=inherited' instead"
        )


def seed(rep: SeedReport) -> None:
    if not required("GOOGLE_CREDENTIALS", "GOOGLE_PROJECT"):
        rep.substitutions.append("gcp: credentials not set - not seeded")
        return
    seed_network(rep)
    seed_firewall(rep)
    seed_disk(rep)
    seed_service_account(rep)
    seed_bucket(rep)
    save_state("gcp", _n())


# --- teardown ----------------------------------------------------------------------------


def teardown(rep: SeedReport) -> None:
    if not required("GOOGLE_CREDENTIALS", "GOOGLE_PROJECT"):
        return
    s, n = settings(), _n()
    if _exists(f"SELECT name FROM google.storage.buckets WHERE bucket='{n['bucket']}'"):
        stackql_exec(f"DELETE FROM google.storage.buckets WHERE bucket='{n['bucket']}'")
        rep.deleted.append(n["bucket"])
    if _exists(
        f"SELECT email FROM google.iam.service_accounts WHERE projectsId='{s.google_project}' AND serviceAccountsId='{n['sa_email']}'"
    ):
        _project_binding(add=False, rep=rep)
        stackql_exec(
            f"DELETE FROM google.iam.service_accounts WHERE projectsId='{s.google_project}' AND serviceAccountsId='{n['sa_email']}'"
        )
        rep.deleted.append(n["sa"])
    else:
        _project_binding(add=False, rep=rep)
    if _exists(
        f"SELECT name FROM google.compute.disks WHERE project='{s.google_project}' AND zone='{s.google_zone}' AND disk='{n['disk']}'"
    ):
        stackql_exec(
            f"DELETE FROM google.compute.disks WHERE project='{s.google_project}' AND zone='{s.google_zone}' AND disk='{n['disk']}'"
        )
        rep.deleted.append(n["disk"])
    if _exists(
        f"SELECT name FROM google.compute.firewalls WHERE project='{s.google_project}' AND firewall='{n['fw']}'"
    ):
        stackql_exec(
            f"DELETE FROM google.compute.firewalls WHERE project='{s.google_project}' AND firewall='{n['fw']}'"
        )
        wait_for(
            "firewall delete",
            lambda: (
                not _exists(
                    f"SELECT name FROM google.compute.firewalls WHERE project='{s.google_project}' AND firewall='{n['fw']}'"
                )
            ),
            timeout=120,
            interval=5,
        )
        rep.deleted.append(n["fw"])
    if _exists(
        f"SELECT name FROM google.compute.networks WHERE project='{s.google_project}' AND network='{n['vpc']}'"
    ):
        wait_for(
            "network delete",
            lambda: _try_delete_network(n["vpc"]),
            timeout=180,
            interval=10,
        )
        rep.deleted.append(n["vpc"])


def _try_delete_network(vpc: str) -> bool:
    s = settings()
    try:
        stackql_exec(
            f"DELETE FROM google.compute.networks WHERE project='{s.google_project}' AND network='{vpc}'"
        )
        return True
    except StackQLError as e:
        return "resourceInUse" not in str(e) and "in use" not in str(e).lower()


def status() -> None:
    s, n = settings(), _n()
    checks = {
        "vpc": f"SELECT name FROM google.compute.networks WHERE project='{s.google_project}' AND network='{n['vpc']}'",
        "firewall": f"SELECT name FROM google.compute.firewalls WHERE project='{s.google_project}' AND firewall='{n['fw']}'",
        "disk": f"SELECT name FROM google.compute.disks WHERE project='{s.google_project}' AND zone='{s.google_zone}' AND disk='{n['disk']}'",
        "service account": f"SELECT email FROM google.iam.service_accounts WHERE projectsId='{s.google_project}' AND serviceAccountsId='{n['sa_email']}'",
        "bucket": f"SELECT name FROM google.storage.buckets WHERE bucket='{n['bucket']}'",
    }
    for k, q in checks.items():
        log("gcp", f"  {k}: {'present' if _exists(q) else 'absent'}")
