"""Snapshot job: materialize the estate into the local backend.

    uv run python -m oape_agents.drift.snapshot [--label text]

For every queries/drift/snapshot_*.sql source, StackQL runs
`CREATE OR REPLACE MATERIALIZED VIEW snap_<ts>_<source> AS <select>` against the file-backed
SQLite backend (SNAPSHOT_DB). Those views hold the raw provider rows. The snapshot job then
normalises them into one table `snapshot_<ts>` (provider, resource_type, resource_key,
state_json, attrs_json) so a delta is a string compare, and registers the snapshot in
snapshots/registry.json. No model is involved: a snapshot costs API calls only.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from oape_agents.common.config import provider_configured, settings
from oape_agents.common.costs import console
from oape_agents.common.queries import StackQLError, list_queries, stackql_exec
from oape_agents.drift import normalize

SOURCES = {
    # query id suffix -> (provider, resource_type, key column, normaliser)
    "snapshot_aws_instances": ("aws", "ec2_instance", "instance_id", normalize.ec2_instance),
    "snapshot_aws_security_groups": ("aws", "security_group", "group_id", normalize.security_group),
    "snapshot_aws_buckets": ("aws", "s3_bucket", "bucket", normalize.s3_bucket),
    "snapshot_azure_nsgs": ("azure", "network_security_group", "id", normalize.azure_nsg),
    "snapshot_google_firewalls": ("google", "firewall", "selfLink", normalize.google_firewall),
}


def db_path() -> Path:
    p = settings().snapshot_db
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def dsn() -> str:
    return f"file:{db_path().as_posix()}"


def registry_path() -> Path:
    return db_path().parent / "registry.json"


def load_registry() -> list[dict]:
    p = registry_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def save_registry(entries: list[dict]) -> None:
    registry_path().write_text(json.dumps(entries, indent=2), encoding="utf-8")


def latest(n: int = 2) -> list[dict]:
    return load_registry()[-n:]


def take(label: str = "") -> dict:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    console.rule(f"[bold]snapshot {ts}[/bold]")
    views: list[str] = []
    counts: dict[str, int] = {}
    t0 = time.time()
    for q in list_queries("drift"):
        suffix = q.id.split("/")[-1]
        if suffix not in SOURCES:
            continue
        if not all(provider_configured(p) for p in q.providers):
            console.print(f"  skip {q.id} (credentials not configured)")
            continue
        params: dict[str, str] = {}
        view = f"snap_{ts}_{suffix.removeprefix('snapshot_')}"
        ddl = f"CREATE OR REPLACE MATERIALIZED VIEW {view} AS {q.render(**params)}"
        try:
            stackql_exec(ddl, backend_dsn=dsn(), timeout=600)
        except StackQLError as e:
            console.print(f"  [red]{q.id}: {str(e)[:200]}[/red]")
            continue
        views.append(view)
    normalised = normalise(ts, views)
    for k, v in normalised.items():
        counts[k] = v
    entry = {
        "ts": ts,
        "table": f"snapshot_{ts}",
        "views": views,
        "counts": counts,
        "taken_at": datetime.now(UTC).isoformat(),
        "label": label,
        "seconds": round(time.time() - t0, 1),
    }
    reg = load_registry()
    reg.append(entry)
    save_registry(reg)
    console.print(
        f"materialized {len(views)} views into {db_path()} in {entry['seconds']}s; "
        f"rows: {counts}; snapshot table snapshot_{ts}"
    )
    return entry


def normalise(ts: str, views: list[str]) -> dict[str, int]:
    """Read the materialized views (plain tables in the backend) and write snapshot_<ts>."""
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    table = f"snapshot_{ts}"
    con.execute(f"DROP TABLE IF EXISTS {table}")
    con.execute(
        f"CREATE TABLE {table} (provider TEXT, resource_type TEXT, resource_key TEXT, "
        f"state_json TEXT, attrs_json TEXT, PRIMARY KEY (resource_type, resource_key))"
    )
    counts: dict[str, int] = {}
    for view in views:
        suffix = "snapshot_" + view.split("_", 2)[2]
        provider, rtype, keycol, fn = SOURCES[suffix]
        n = 0
        for row in con.execute(f'SELECT * FROM "{view}"'):
            d = dict(row)
            key = d.get(keycol)
            if key in (None, "", "null"):
                continue
            attrs = fn(d)
            con.execute(
                f"INSERT OR REPLACE INTO {table} VALUES (?, ?, ?, ?, ?)",
                (provider, rtype, str(key), json.dumps(d, default=str), normalize.stable(attrs)),
            )
            n += 1
        counts[rtype] = n
    con.commit()
    con.close()
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="")
    a = ap.parse_args()
    take(a.label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
