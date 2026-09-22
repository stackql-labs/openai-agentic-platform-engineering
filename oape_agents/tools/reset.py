"""`make reset`: put the demo back to its pre-run state between rehearsals.

- closes every open GitHub issue the sweeps filed (label oape-demo)
- scales the checkout ASG back to desired=1 (undoes the triage mutation)
- restores the Terraform-managed resources (undoes `make tf-perturb`)
- clears runs/ (keeps the MCP audit log unless --clear-audit)

    uv run python -m oape_agents.tools.reset [--keep-issues] [--keep-perturbation] [--clear-audit]
"""

from __future__ import annotations

import argparse
import shutil
import sys

from oape_agents.common import github_out
from oape_agents.common.config import RUNS_DIR, settings
from oape_agents.common.costs import console


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-issues", action="store_true")
    ap.add_argument("--keep-perturbation", action="store_true")
    ap.add_argument("--clear-audit", action="store_true")
    a = ap.parse_args()
    s = settings()
    console.rule("[bold]reset[/bold]")
    if not a.keep_issues:
        try:
            n = github_out.close_demo_issues()
            console.print(f"closed {n} open demo issue(s) in {s.github_issues_repo}")
        except Exception as e:  # noqa: BLE001
            console.print(f"[yellow]could not close issues: {e}[/yellow]")
    try:
        from seed.aws.seed_aws import scale_checkout

        scale_checkout(1)
    except Exception as e:  # noqa: BLE001
        console.print(f"[yellow]could not reset the checkout ASG: {e}[/yellow]")
    if not a.keep_perturbation:
        try:
            from seed.terraform.perturb import perturb

            perturb(restore=True)
        except Exception as e:  # noqa: BLE001
            console.print(f"[yellow]could not restore terraform-managed resources: {e}[/yellow]")
    if RUNS_DIR.exists():
        for p in RUNS_DIR.iterdir():
            if p.name == s.mcp_audit_log.name and not a.clear_audit:
                continue
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        console.print(f"cleared {RUNS_DIR} (audit log {'cleared' if a.clear_audit else 'kept'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
