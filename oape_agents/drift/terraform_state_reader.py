"""terraform_state_reader: parse the local Terraform state for seed/terraform and load the intended
state into the snapshot backend as `tfstate_resources`, normalised with the same functions the live
snapshots use, so drift/tfstate_vs_live.sql is a plain comparison.

    uv run python -m oape_agents.drift.terraform_state_reader [--state path]

Not a StackQL feature (see WORK_ORDER.md): the state file is read locally, never through a
provider, and nothing here talks to a cloud API.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from oape_agents.common.config import REPO_ROOT
from oape_agents.common.costs import console
from oape_agents.drift import normalize
from oape_agents.drift.snapshot import db_path

DEFAULT_STATE = REPO_ROOT / "seed" / "terraform" / "terraform.tfstate"


def read(state_path: Path = DEFAULT_STATE) -> list[dict]:
    """Return rows: provider, resource_type, resource_key, tf_address, attrs_json."""
    if not state_path.exists():
        raise FileNotFoundError(f"no tfstate at {state_path} - run `make tf-apply` first")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("version") != 4:
        raise ValueError(f"unsupported tfstate version {state.get('version')}")
    rows: list[dict] = []
    for res in state.get("resources", []):
        if res.get("mode") != "managed":
            continue
        address = f"{res['type']}.{res['name']}"
        for inst in res.get("instances", []):
            a = inst.get("attributes", {})
            if res["type"] == "aws_instance":
                rows.append(("aws", "ec2_instance", a["id"], address, normalize.tf_instance(a)))
            elif res["type"] == "aws_security_group":
                rows.append(
                    ("aws", "security_group", a["id"], address, normalize.tf_security_group(a))
                )
            elif res["type"] == "aws_s3_bucket":
                rows.append(("aws", "s3_bucket", a["bucket"], address, normalize.tf_bucket(a)))
    return [
        {
            "provider": p,
            "resource_type": t,
            "resource_key": k,
            "tf_address": addr,
            "attrs_json": normalize.stable(attrs),
        }
        for p, t, k, addr, attrs in rows
    ]


def load(rows: list[dict]) -> int:
    con = sqlite3.connect(db_path())
    con.execute("DROP TABLE IF EXISTS tfstate_resources")
    con.execute(
        "CREATE TABLE tfstate_resources (provider TEXT, resource_type TEXT, resource_key TEXT, "
        "tf_address TEXT, attrs_json TEXT, PRIMARY KEY (resource_type, resource_key))"
    )
    con.executemany(
        "INSERT INTO tfstate_resources VALUES (?, ?, ?, ?, ?)",
        [
            (r["provider"], r["resource_type"], r["resource_key"], r["tf_address"], r["attrs_json"])
            for r in rows
        ],
    )
    con.commit()
    con.close()
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default=str(DEFAULT_STATE))
    a = ap.parse_args()
    rows = read(Path(a.state))
    n = load(rows)
    console.print(f"loaded {n} intended resources from {a.state} into tfstate_resources")
    for r in rows:
        console.print(
            f"  {r['tf_address']} -> {r['resource_type']} {r['resource_key']} {r['attrs_json'][:120]}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
