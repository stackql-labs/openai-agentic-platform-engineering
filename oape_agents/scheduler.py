"""Always-on wrapper: run the three sweeps every SWEEP_SCHEDULE_MINUTES until interrupted.

    uv run python -m oape_agents.scheduler [--once] [--dry-run]

A cron entry or a systemd timer calling the make targets is equivalent; this loop exists so the
demo can point at a process that has been running on its own.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import UTC, datetime

from oape_agents.common.config import env_int
from oape_agents.common.costs import console
from oape_agents.sweeps import cspm, entitlements, finops
from oape_agents.sweeps.base import run_sweep

SWEEPS = [cspm.SPEC, entitlements.SPEC, finops.SPEC]


async def tick(dry_run: bool) -> None:
    for spec in SWEEPS:
        started = datetime.now(UTC).isoformat()
        console.rule(f"[bold]scheduler: {spec.scenario} at {started}[/bold]")
        try:
            await run_sweep(spec, fallback=False, dry_run=dry_run, record=False)
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]{spec.scenario} failed: {e}[/red]")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    interval = env_int("SWEEP_SCHEDULE_MINUTES", 60)
    while True:
        asyncio.run(tick(a.dry_run))
        if a.once:
            return 0
        console.print(f"next sweep in {interval} minutes (ctrl-c to stop)")
        time.sleep(interval * 60)


if __name__ == "__main__":
    sys.exit(main())
