"""`make validate-queries`: parse every file under queries/, render it with the tenancy parameters
(plus placeholder values for fan-out lists), plan it with `stackql exec --dryrun`, and with `--live`
execute it and check the returned columns against the header's expected_columns.

    uv run python -m oape_agents.tools.validate_queries [--live] [--scenario cspm] [--only id]

Mutation templates (UPDATE/EXEC/INSERT/DELETE) are parsed and rendered but never planned or run
here: the only executable path for them is the approval gate.
"""

from __future__ import annotations

import argparse
import sys
import time

from oape_agents.common.config import provider_configured, settings
from oape_agents.common.costs import console
from oape_agents.common.queries import (
    Query,
    StackQLError,
    list_queries,
    render_union,
    sql_list,
    stackql_exec,
    stackql_validate,
)

# Placeholder values for parameters that are computed at run time (fan-out lists, ids). They only
# need to be syntactically valid for planning; --live substitutes real values from the estate.
PLACEHOLDERS = {
    "s3_bucket_list": "'example-bucket'",
    "aws_region_list": "'us-east-1'",
    "github_repo_list": "'example-repo'",
    "iam_user_name": "example-user",
    "gcs_bucket_name": "example-bucket",
    "aws_admins_sql": "SELECT 'example-user' AS user_name",
    "user_name": "example-user",
    "idp_group_id": "00000000-0000-0000-0000-000000000000",
    "target_group_arn": "arn:aws:elasticloadbalancing:us-east-1:000000000000:targetgroup/x/0",
    "project_id": "proj_example",
    "cost_start_time": "1700000000",
    "desired_capacity": "1",
    "prev_view": "snapshot_prev",
    "curr_view": "snapshot_curr",
}

MUTATION_VERBS = ("UPDATE ", "INSERT ", "DELETE ", "EXEC ", "REPLACE ")

# Known tenancy gaps recorded in WORK_ORDER.md: a provider that answers with this status is
# reported as blocked, not failed, so the rest of the library still gates on green.
BLOCKED_PROVIDERS = {"entra_id": "403"}


def live_params(q: Query) -> dict[str, str]:
    """Resolve fan-out parameters from the live estate for --live runs."""
    s = settings()
    p: dict[str, str] = {}
    if "s3_bucket_list" in q.params:
        rows = stackql_exec(_render("cspm/aws_s3_buckets_in_scope"))
        p["s3_bucket_list"] = sql_list([r["bucket"] for r in rows]) or "'none'"
    if "aws_region_list" in q.params:
        rows = stackql_exec(_render("cspm/aws_regions"))
        p["aws_region_list"] = sql_list([r["region_name"] for r in rows])
    if "github_repo_list" in q.params:
        rows = stackql_exec(_render("cspm/github_repos"))
        names = [r["name"] for r in rows if r["name"].startswith(s.demo_prefix)]
        p["github_repo_list"] = sql_list(names) or "'none'"
    if "iam_user_name" in q.params:
        p["iam_user_name"] = f"{s.demo_prefix}-admin-user"
    if "gcs_bucket_name" in q.params:
        p["gcs_bucket_name"] = f"{s.demo_prefix}-public-{s.google_project}"
    if "aws_admins_sql" in q.params:
        p["aws_admins_sql"] = render_union(
            "entitlements/aws_admin_user_literal", "user_name", [f"{s.demo_prefix}-admin-user"]
        )
    if "target_group_arn" in q.params:
        rows = stackql_exec(_render("triage/aws_target_groups"))
        p["target_group_arn"] = (
            rows[0]["target_group_arn"] if rows else PLACEHOLDERS["target_group_arn"]
        )
    if "cost_start_time" in q.params:
        p["cost_start_time"] = str(int(time.time()) - 7 * 86400)
    for k in q.params:
        p.setdefault(k, PLACEHOLDERS.get(k, ""))
    return {k: v for k, v in p.items() if v}


def _render(query_id: str, **params: str) -> str:
    from oape_agents.common.queries import load_query

    return load_query(query_id).render(**params)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--live", action="store_true", help="execute and check columns (needs seeded estate)"
    )
    ap.add_argument("--scenario", default=None)
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    queries = list_queries(a.scenario)
    if a.only:
        queries = [q for q in queries if q.id == a.only]
    ok = 0
    failed: list[str] = []
    skipped: list[str] = []
    for q in queries:
        is_mutation = q.sql.lstrip().upper().startswith(MUTATION_VERBS)
        if not all(provider_configured(p) for p in q.providers):
            skipped.append(f"{q.id} (credentials for {q.providers} not configured)")
            continue
        try:
            params = live_params(q) if a.live else {k: PLACEHOLDERS.get(k, "") for k in q.params}
            params = {k: v for k, v in params.items() if v}
            sql = q.render(**params)
        except Exception as e:  # noqa: BLE001
            failed.append(f"{q.id}: render: {e}")
            continue
        if is_mutation:
            console.print(f"[cyan]{q.id}[/cyan] mutation template rendered (not planned, not run)")
            ok += 1
            continue
        if q.scenario == "drift" and not a.live:
            console.print(
                f"[cyan]{q.id}[/cyan] snapshot/delta query rendered (runs against the snapshot backend)"
            )
            ok += 1
            continue
        if a.live:
            t0 = time.time()
            try:
                rows = stackql_exec(sql, timeout=600)
            except StackQLError as e:
                blocked = [
                    p
                    for p in q.providers
                    if p in BLOCKED_PROVIDERS and BLOCKED_PROVIDERS[p] in str(e)
                ]
                if blocked:
                    skipped.append(
                        f"{q.id} (blocked: {blocked[0]} returned {BLOCKED_PROVIDERS[blocked[0]]} - see WORK_ORDER.md)"
                    )
                    continue
                failed.append(f"{q.id}: {str(e)[:300]}")
                continue
            cols = sorted(rows[0].keys()) if rows else []
            missing = [c for c in q.expected_columns if rows and c not in rows[0]]
            extra = [c for c in cols if c not in q.expected_columns] if rows else []
            status = "ok" if not missing else f"missing columns {missing}"
            if extra:
                status += f" (extra columns {extra})"
            console.print(
                f"[{'green' if not missing else 'red'}]{q.id}[/] {len(rows)} rows in {time.time() - t0:.1f}s - {status}"
            )
            if missing:
                failed.append(f"{q.id}: expected columns not returned: {missing}")
            else:
                ok += 1
        else:
            valid, err = stackql_validate(sql)
            if valid:
                console.print(f"[green]{q.id}[/green] plan ok")
                ok += 1
            else:
                failed.append(f"{q.id}: {err[:300]}")
    console.rule()
    console.print(f"{ok} ok, {len(failed)} failed, {len(skipped)} skipped")
    for s in skipped:
        console.print(f"  skipped: {s}")
    for f in failed:
        console.print(f"  [red]failed: {f}[/red]")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
