"""Azure seed - a stackql-deploy stack (seed/azure/stackql_manifest.yml + resources/*.iql).

`seed` runs `stackql-deploy build`, `teardown` runs `stackql-deploy teardown` (which deletes the
resource group and everything in it). Both are idempotent by construction: build is
exists -> statecheck -> create per resource; teardown of an absent stack is a no-op.

Substitution (WORK_ORDER.md): no Owner role assignment is planted - the demo service principal
holds Contributor and cannot write role assignments. The detection query reports the Owner
assignments that already exist at subscription scope.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from oape_agents.common.config import ENV_FILE, REPO_ROOT, settings
from oape_agents.common.queries import stackql_exec
from seed.common import SeedReport, log, required

STACK_DIR = Path(__file__).resolve().parent
STACK_ENV = "dev"
DOWNLOAD_DIR = REPO_ROOT / ".stackql" / "stackql-deploy"


def _deploy(cmd: str, extra: list[str] | None = None) -> int:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        sys.executable,
        "-m",
        "stackql_deploy.cli",
        cmd,
        str(STACK_DIR),
        STACK_ENV,
        "--env-file",
        str(ENV_FILE),
        "--download-dir",
        str(DOWNLOAD_DIR),
        "--log-level",
        "INFO",
        *(extra or []),
    ]
    env = dict(os.environ)
    env.update({"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"})
    proc = subprocess.run(
        args, text=True, capture_output=True, env=env, encoding="utf-8", errors="replace"
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    for line in out.splitlines()[-40:]:
        log("stackql-deploy", line.rstrip())
    return proc.returncode


def _rg_exists() -> bool:
    s = settings()
    rows = stackql_exec(
        "SELECT COUNT(*) AS n FROM azure.resource.resource_groups "
        f"WHERE subscription_id = '{s.azure_subscription_id}' AND resource_group_name = '{s.demo_prefix}-rg'"
    )
    return bool(rows) and str(rows[0].get("n", "0")) not in ("0", "")


def seed(rep: SeedReport) -> None:
    if not required(
        "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_SUBSCRIPTION_ID"
    ):
        rep.substitutions.append("azure: credentials not set - not seeded")
        return
    rc = _deploy("build")
    if rc != 0:
        raise RuntimeError(f"stackql-deploy build exited {rc}")
    rep.created.append(f"{settings().demo_prefix}-rg (stack build converged)")
    rep.substitutions.append(
        "azure: Owner role assignment at subscription scope not planted (service principal is Contributor); "
        "the detection query reports existing Owner assignments"
    )


def teardown(rep: SeedReport) -> None:
    if not required(
        "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET", "AZURE_SUBSCRIPTION_ID"
    ):
        return
    if not _rg_exists():
        log("azure", "resource group absent - nothing to tear down", "skip")
        return
    rc = _deploy("teardown")
    if rc != 0:
        raise RuntimeError(f"stackql-deploy teardown exited {rc}")
    rep.deleted.append(f"{settings().demo_prefix}-rg")


def status() -> None:
    s = settings()
    if not _rg_exists():
        log("azure", "resource group absent")
        return
    rows = stackql_exec(
        "SELECT name, type FROM azure.resource.resources "
        f"WHERE subscription_id = '{s.azure_subscription_id}' AND resource_group_name = '{s.demo_prefix}-rg'"
    )
    for r in rows:
        log("azure", f"  {r.get('type')} {r.get('name')}")
