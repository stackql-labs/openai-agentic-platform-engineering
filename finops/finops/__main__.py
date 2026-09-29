"""Command line entry point.

    uv run python -m finops setup    [--providers aws,azure,google]
    uv run python -m finops validate [--providers ...]
    uv run python -m finops run      [--providers ...] [--skip-reasoning] [--snapshot-max-age-days N]

Run from inside finops/ (or `uv run finops ...` via the console script). `.env` is read from the
repo root.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from .config import PROVIDERS, settings
from .costs import console
from .mcp import call, read_only_server
from .queries import list_queries


def _providers_arg(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    return [p.strip() for p in raw.split(",") if p.strip()]


async def cmd_setup(providers: list[str]) -> int:
    """Pull the providers into the approot through the MCP tool pull_provider (one call per
    provider) and print server_info. No model is involved; the server is still read_only."""
    s = settings()
    console.print(f"approot: {s.stackql_approot}")
    async with read_only_server("stackql-setup", filtered=False, timeout_seconds=900) as server:
        for p in providers:
            console.print(f"pull_provider {p} ...")
            out = await call(server, "pull_provider", {"provider": p})
            console.print(f"  {json.dumps(out, default=str)[:300]}")
        info = await call(server, "server_info")
        console.rule("server_info")
        console.print(json.dumps(info, indent=2, default=str))
        mode = info.get("mode") if isinstance(info, dict) else None
        if mode not in (None, "read_only"):
            console.print(f"[red]server mode is {mode}, expected read_only[/red]")
            return 1
    return 0


async def cmd_validate(providers: list[str]) -> int:
    """validate_select_query for every SELECT in finops/queries/ whose providers are in scope."""
    failures = 0
    async with read_only_server("stackql-validate", filtered=False) as server:
        for q in list_queries(kind="select"):
            if not all(p in providers for p in q.providers):
                console.print(f"skip  {q.id} (providers {q.providers} not selected)")
                continue
            sql = q.render()
            out = await call(server, "validate_select_query", {"sql": sql, "format": "json"})
            valid = bool(out.get("valid")) if isinstance(out, dict) else False
            if valid:
                console.print(f"[green]pass[/]  {q.id}")
            else:
                failures += 1
                errors = out.get("errors") if isinstance(out, dict) else out
                console.print(f"[red]FAIL[/]  {q.id}: {errors}")
    console.print(f"{failures} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="finops",
        description="FinOps cost optimisation audit: a scheduled, read-only sweep for idle and "
        "orphaned resources across AWS, Azure and Google Cloud via the StackQL MCP server.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    prov_help = f"comma-separated subset of {','.join(PROVIDERS)} (default: every provider with credentials in .env)"

    p_setup = sub.add_parser("setup", help="pull providers into the approot and print server_info")
    p_setup.add_argument("--providers", help=prov_help)

    p_val = sub.add_parser("validate", help="validate_select_query for every SELECT in queries/")
    p_val.add_argument("--providers", help=prov_help)

    p_run = sub.add_parser("run", help="run the sweep once")
    p_run.add_argument("--providers", help=prov_help)
    p_run.add_argument(
        "--skip-reasoning",
        action="store_true",
        help="stop after the sweep tier (no proposed statements)",
    )
    p_run.add_argument(
        "--snapshot-max-age-days", type=int, help="override SNAPSHOT_MAX_AGE_DAYS for this run"
    )

    a = ap.parse_args(argv)
    if getattr(a, "snapshot_max_age_days", None) is not None:
        os.environ["SNAPSHOT_MAX_AGE_DAYS"] = str(a.snapshot_max_age_days)
        settings.cache_clear()

    requested = _providers_arg(a.providers)
    if a.cmd == "setup":
        providers = requested or list(PROVIDERS)
        return asyncio.run(cmd_setup(providers))

    from .sweep import run_sweep, select_providers

    if a.cmd == "validate":
        return asyncio.run(cmd_validate(requested or list(PROVIDERS)))
    providers = select_providers(requested)
    return asyncio.run(run_sweep(providers, skip_reasoning=a.skip_reasoning))


if __name__ == "__main__":
    sys.exit(main())
