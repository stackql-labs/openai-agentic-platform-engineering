"""Seed orchestrator.

    python -m seed.run seed      [--only aws,azure,gcp,github,idp,openai]
    python -m seed.run teardown  [--only ...]
    python -m seed.run check     - one detection query per planted misconfiguration
    python -m seed.run status    - what exists right now

Provider order on seed: aws, azure, gcp, github, idp, openai. Teardown reverses it. Each provider
module exposes seed(report) / teardown(report) / status() and is independent - a failure in one
does not stop the others; the exit code is non-zero if any errored.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import traceback

from seed.common import SeedReport, console, log

PROVIDERS = ["aws", "azure", "gcp", "github", "idp", "openai"]


def _module(p: str):
    return importlib.import_module(f"seed.{p}.seed_{p}")


def run(action: str, only: list[str]) -> int:
    order = [p for p in PROVIDERS if p in only]
    if action == "teardown":
        order = list(reversed(order))
    reports: list[SeedReport] = []
    for p in order:
        console.rule(f"[bold]{action}: {p}[/bold]")
        rep = SeedReport(provider=p)
        try:
            mod = _module(p)
            getattr(mod, action)(rep)
        except Exception as e:  # noqa: BLE001
            rep.errors.append(f"{type(e).__name__}: {e}")
            log(p, f"{type(e).__name__}: {e}", "err")
            traceback.print_exc()
        reports.append(rep)
    console.rule("[bold]summary[/bold]")
    for r in reports:
        console.print(r.summary())
        for s in r.substitutions:
            console.print(f"  substitution: {s}")
        for e in r.errors:
            console.print(f"  [red]error: {e}[/red]")
    return 1 if any(r.errors for r in reports) else 0


def status(only: list[str]) -> int:
    for p in [x for x in PROVIDERS if x in only]:
        console.rule(f"[bold]status: {p}[/bold]")
        try:
            _module(p).status()
        except Exception as e:  # noqa: BLE001
            log(p, f"{type(e).__name__}: {e}", "err")
    return 0


def check(only: list[str]) -> int:
    from seed.check import run_checks

    return run_checks(only)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["seed", "teardown", "check", "status"])
    ap.add_argument("--only", default=",".join(PROVIDERS))
    a = ap.parse_args()
    only = [x.strip() for x in a.only.split(",") if x.strip()]
    if a.action in ("seed", "teardown"):
        return run(a.action, only)
    if a.action == "status":
        return status(only)
    return check(only)


if __name__ == "__main__":
    sys.exit(main())
