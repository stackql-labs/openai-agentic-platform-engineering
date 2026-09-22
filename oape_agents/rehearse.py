"""`make rehearse`: the full demo running order against fallbacks, no live APIs, with cumulative
timings. `--record` re-records every fallback from live runs instead (needs the seeded estate,
an operator at the triage gate unless --approve, and a perturbed Terraform subset for drift).

    uv run python -m oape_agents.rehearse [--no-pause] [--record [--approve]]

Running order (runbooks/00_master.md): hook -> cspm -> entitlements -> triage -> finops -> drift
-> openai estate.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

from oape_agents.common.config import REPO_ROOT
from oape_agents.common.costs import console

ORDER = [
    ("hook", "oape_agents.smoke", 2),
    ("cspm", "oape_agents.sweeps.cspm", 6),
    ("entitlements", "oape_agents.sweeps.entitlements", 4),
    ("triage", "oape_agents.triage.triage", 6),
    ("finops", "oape_agents.sweeps.finops", 4),
    ("drift", "oape_agents.drift.brief", 4),
    ("openai_estate", "oape_agents.openai_estate.estate", 3),
]


def run_step(module: str, args: list[str]) -> int:
    cmd = [sys.executable, "-m", module, *args]
    return subprocess.call(cmd, cwd=REPO_ROOT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-pause", action="store_true")
    ap.add_argument("--record", action="store_true", help="record fallbacks from live runs")
    ap.add_argument("--approve", action="store_true", help="with --record: approve the triage gate")
    a = ap.parse_args()
    t0 = time.time()
    budget = 0
    failures: list[str] = []
    for name, module, minutes in ORDER:
        budget += minutes
        console.rule(
            f"[bold]{name}[/bold]  (segment budget {minutes} min, cumulative {budget} min)"
        )
        if a.record:
            args = ["--record"]
            if name == "triage" and a.approve:
                args.append("--approve")
        else:
            args = ["--fallback"]
        rc = run_step(module, args)
        if rc != 0:
            failures.append(f"{name} (exit {rc})")
        elapsed = (time.time() - t0) / 60
        console.print(
            f"[dim]{name} done - wall clock {elapsed:.1f} min of {budget} min budget[/dim]"
        )
        if not a.no_pause and not a.record:
            console.print("press enter for the next segment", end="")
            try:
                input()
            except EOFError:
                pass
    console.rule()
    console.print(
        f"rehearsal complete in {(time.time() - t0) / 60:.1f} min; running-order budget {budget} min"
    )
    if failures:
        console.print(f"[red]segments that failed: {failures}[/red]")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
