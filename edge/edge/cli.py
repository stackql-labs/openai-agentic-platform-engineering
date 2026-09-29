"""Command line entry point: `uv run python -m edge <setup|validate|run|restore> [flags]`.

run:     recon (mini tier, read-only server) -> decision (frontier tier, no tools) -> approval
         gate -> one REPLACE -> verify -> decision record -> cost/trace block
restore: put BASELINE_THRESHOLD back through the same gate (no model calls)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from agents import gen_trace_id, trace
from rich.markup import escape
from rich.table import Table

from . import gate, records
from .config import ENV_FILE, ConfigError, settings
from .costs import RunLedger, console
from .decide import Decision, Policy, rollback_statement, run_decision
from .mcp import read_only_server, stackql_mcp_command, stackql_server, tool_is_error, tool_text
from .queries import list_queries
from .recon import ReconReport, run_recon

TS = "%Y-%m-%dT%H:%M:%SZ"
PROVIDERS = ("cloudflare", "github")
UPSTREAM_MARKERS = ("Authentication failed", "upstream http error", "http_status")


def _add_gate_flags(p: argparse.ArgumentParser) -> None:
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--approve",
        action="store_true",
        help="approve every gated statement without the terminal prompt (unattended runs)",
    )
    g.add_argument(
        "--decline",
        action="store_true",
        help="decline every gated statement (proves the abort path; nothing is mutated)",
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="edge",
        description=(
            "Edge autopilot: read live Cloudflare zone analytics and the rate limit ruleset, "
            "decide whether traffic is elevated, tighten the rate limit behind an approval gate, "
            "and log the decision. Reads the repo-root .env."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser(
        "setup",
        help="pull the cloudflare and github providers into STACKQL_APPROOT and print server_info",
    )
    sub.add_parser(
        "validate", help="run validate_select_query for every SELECT under edge/queries/"
    )
    run = sub.add_parser(
        "run",
        help="recon -> decision -> approval gate -> REPLACE -> verify -> decision record",
    )
    _add_gate_flags(run)
    run.add_argument(
        "--window-minutes",
        type=int,
        default=None,
        help="recon window ending now (default WINDOW_MINUTES, 30)",
    )
    run.add_argument(
        "--elevated-rps",
        type=float,
        default=None,
        help="requests per second at or above which the policy tightens (default ELEVATED_RPS)",
    )
    restore = sub.add_parser(
        "restore", help="put BASELINE_THRESHOLD back on the demo rule through the same gate"
    )
    _add_gate_flags(restore)
    return p


def _resolve_approval(
    pending: gate.PendingMutation, args: argparse.Namespace
) -> gate.Approval | None:
    if args.decline:
        gate.declined(pending)
        return None
    if args.approve:
        return gate.flag_approval(pending)
    return gate.ask_terminal(pending)


def _tool_json(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structuredContent", None) or getattr(
        result, "structured_content", None
    )
    if isinstance(structured, dict):
        return structured
    text = tool_text(result).strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return {}


def _require_zone() -> str:
    zone = settings().cloudflare_zone_id
    if not zone:
        raise ConfigError(f"CLOUDFLARE_ZONE_ID is not set - add it to {ENV_FILE}")
    return zone


# --- setup ---------------------------------------------------------------------------------------


async def cmd_setup() -> int:
    s = settings()
    console.print(f"stackql-mcp launcher: {stackql_mcp_command()}")
    console.print(f"approot: {s.stackql_approot}")
    console.print(f"audit log: {s.mcp_audit_log}")
    server = stackql_server(
        name="stackql-setup",
        mode="read_only",
        allowed_tools=("server_info", "pull_provider"),
        timeout_seconds=600.0,
    )
    async with server:
        for provider in PROVIDERS:
            res = await server.call_tool("pull_provider", {"provider": provider})
            text = tool_text(res).strip()
            if tool_is_error(res):
                console.print(f"[red]pull_provider {provider} failed:[/red] {text[:400]}")
                return 1
            console.print(text[:300])
        res = await server.call_tool("server_info", {})
        console.print(tool_text(res).strip())
    return 0


# --- validate ------------------------------------------------------------------------------------


async def cmd_validate() -> int:
    now = datetime.now(UTC).replace(microsecond=0)
    fixed = {
        "since": (now - timedelta(minutes=30)).strftime(TS),
        "until": now.strftime(TS),
        "demo_prefix": settings().demo_prefix,
    }
    selects = [q for q in list_queries() if q.is_select]
    server = stackql_server(
        name="stackql-validate", mode="read_only", allowed_tools=("validate_select_query",)
    )
    failures = 0
    planned = 0
    async with server:
        for q in selects:
            params = {
                p: fixed.get(p) or os.environ.get(p.upper()) or f"placeholder-{p}" for p in q.params
            }
            res = await server.call_tool(
                "validate_select_query", {"sql": q.render(**params), "format": "json"}
            )
            body = _tool_json(res)
            valid = bool(body.get("valid")) and not tool_is_error(res)
            errors = [str(e) for e in (body.get("errors") or [])]
            if not valid and not errors:
                errors = [tool_text(res)[:300]]
            # validate_select_query plans and then executes: with placeholder credentials the
            # provider call fails after planning, which is not a query error
            upstream_only = bool(errors) and all(
                any(m in e for m in UPSTREAM_MARKERS) for e in errors
            )
            if valid:
                mark = "[green]PASS[/green]"
            elif upstream_only:
                planned += 1
                mark = (
                    "[yellow]PLANNED[/yellow] (upstream auth error with the configured credentials)"
                )
            else:
                failures += 1
                mark = "[red]FAIL[/red]"
            console.print(f"{mark} {q.id}  {'' if valid else escape(str(errors)[:400])}")
    console.print(
        f"{len(selects) - failures}/{len(selects)} SELECT queries plan "
        f"({planned} could not execute with the configured credentials)"
    )
    return 0 if failures == 0 else 1


# --- run -----------------------------------------------------------------------------------------


def _print_report(report: ReconReport, policy: Policy) -> None:
    t = Table(title="recon report", show_header=False)
    t.add_column("field")
    t.add_column("value", justify="right")
    t.add_row("total requests", f"{report.total_requests:,}")
    t.add_row("distinct countries", str(report.distinct_countries))
    t.add_row("top countries", ", ".join(report.top_countries) or "-")
    t.add_row("non-2xx share", f"{report.non_2xx_share:.1%} ({report.non_2xx_requests:,})")
    t.add_row(
        "requests per second",
        f"{report.requests_per_second:.3f} (elevated at {policy.elevated_rps})",
    )
    t.add_row("ruleset / rule", f"{report.ruleset_id or '-'} / {report.rule_id or '-'}")
    t.add_row("threshold / period", f"{report.threshold} per {report.period}s")
    console.print(t)
    if report.notes:
        console.print(f"notes: {report.notes}")


def _print_decision(decision: Decision) -> None:
    console.print(f"decision: [bold]{decision.action}[/bold] -> threshold {decision.new_threshold}")
    console.print(f"rationale: {decision.rationale}")
    if decision.risk:
        console.print(f"risk: {decision.risk}")


async def _write_record(
    record: records.DecisionRecord, args: argparse.Namespace, ledger: RunLedger
) -> dict[str, Any]:
    """jsonl by default; a GitHub issue through the gate when GITHUB_DECISIONS_REPO is set."""
    s = settings()
    if s.github_decisions_repo:
        owner, repo = records.split_repo(s.github_decisions_repo)
        console.rule("decision record - GitHub issue (second gated statement)")
        pending = gate.prepare(
            "file_decision_issue",
            records.issue_params(record, owner, repo),
            f"{record.record_id}-record",
        )
        approval = _resolve_approval(pending, args)
        if approval is not None:
            try:
                out = await gate.execute_approved(
                    pending, approval, assert_target=gate.assert_decisions_repo(owner, repo)
                )
                ledger.gate_statements += 1
                record.sink = "github-issue"
                console.print(f"decision record filed as an issue in {owner}/{repo}")
                return {"sink": "github-issue", "repo": f"{owner}/{repo}", "gate": out}
            except (gate.TargetAssertionFailed, RuntimeError) as e:
                console.print(f"[red]issue not filed:[/red] {e}")
                ledger.notes.append(f"github issue not filed: {e}")
        ledger.notes.append("decision record fell back to jsonl")
    path = records.append_jsonl(record)
    console.print(f"decision record appended: {path}")
    return {"sink": "jsonl", "path": str(path)}


async def cmd_run(args: argparse.Namespace) -> int:
    s = settings()
    zone = _require_zone()
    window_minutes = args.window_minutes or s.window_minutes
    policy = Policy(
        elevated_rps=args.elevated_rps if args.elevated_rps is not None else s.elevated_rps,
        tightened_threshold=s.tightened_threshold,
        baseline_threshold=s.baseline_threshold,
        period=s.rate_limit_period,
    )
    ledger = RunLedger("edge", trace_id=gen_trace_id())
    until = datetime.now(UTC).replace(microsecond=0)
    since = until - timedelta(minutes=window_minutes)
    since_s, until_s = since.strftime(TS), until.strftime(TS)
    proposal_id = f"edge-{until.strftime('%Y%m%dT%H%M%SZ')}"
    gate_result: dict[str, Any] | None = None
    rc = 0

    with trace("edge autopilot", trace_id=ledger.trace_id):
        console.rule("recon - SWEEP_MODEL on a read-only server")
        console.print(f"zone {zone}  window {since_s} -> {until_s} ({window_minutes} min)")
        async with read_only_server("stackql-ro") as ro:
            report = await run_recon(ro, since_s, until_s, window_minutes * 60, ledger)
        _print_report(report, policy)

        console.rule("decision - REASONING_MODEL, no tools")
        decision = await run_decision(report, policy, ledger)
        _print_decision(decision)

        record = records.DecisionRecord(
            record_id=proposal_id,
            zone_id=zone,
            action=decision.action,
            outcome="no-change",
            previous_threshold=report.threshold,
            new_threshold=decision.new_threshold,
            period=report.period or policy.period,
            window_since=since_s,
            window_until=until_s,
            total_requests=report.total_requests,
            requests_per_second=report.requests_per_second,
            elevated_rps=policy.elevated_rps,
            non_2xx_share=report.non_2xx_share,
            rationale=decision.rationale,
            risk=decision.risk,
            trace_id=ledger.trace_id,
        )

        if decision.action == "tighten":
            pending = gate.prepare(
                "set_rate_limit_threshold",
                {"threshold": decision.new_threshold, "demo_prefix": s.demo_prefix},
                proposal_id,
            )
            record.rollback_statement = rollback_statement(report.threshold)
            approval = _resolve_approval(pending, args)
            if approval is None:
                record.outcome = "declined"
            else:
                try:
                    gate_result = await gate.execute_approved(
                        pending,
                        approval,
                        assert_target=gate.assert_demo_rule(),
                        verify=gate.verify_threshold(decision.new_threshold),
                    )
                    ledger.gate_statements += 1
                    record.outcome = "executed"
                    record.statement = pending.sql
                    record.approved_by = approval.approver
                    record.approval_method = approval.method
                    console.print(f"rollback: {record.rollback_statement}")
                except (gate.TargetAssertionFailed, gate.VerificationFailed, RuntimeError) as e:
                    console.print(f"[red]{type(e).__name__}:[/red] {e}")
                    record.outcome = f"failed: {e}"
                    rc = 1
        else:
            console.print("hold - no statement proposed, nothing to approve")

        sink = await _write_record(record, args, ledger)

    ledger.print_summary()
    path = ledger.save(
        {
            "recon": report.model_dump(),
            "decision": decision.model_dump(),
            "gate": gate_result,
            "record": record.model_dump(),
            "record_sink": sink,
        }
    )
    console.print(f"run record: {path}")
    return rc


# --- restore -------------------------------------------------------------------------------------


async def cmd_restore(args: argparse.Namespace) -> int:
    s = settings()
    zone = _require_zone()
    ledger = RunLedger("edge-restore")
    proposal_id = f"edge-restore-{ledger.started_at.strftime('%Y%m%dT%H%M%SZ')}"
    rc = 0

    console.rule("restore - current rule (read-only server)")
    try:
        async with read_only_server("stackql-ro") as ro:
            row = await gate.read_rate_limit_rule(ro)
    except gate.TargetAssertionFailed as e:
        console.print(f"[red]cannot read the rate limit rule:[/red] {escape(str(e))}")
        return 1
    try:
        previous = int(float(row.get("threshold")))
    except (TypeError, ValueError):
        previous = 0
    period = int(float(row.get("period") or s.rate_limit_period))
    console.print(
        f"zone {zone}: rule {row.get('rule_id') or '-'} ({row.get('description') or '-'}) "
        f"threshold {previous} per {period}s -> baseline {s.baseline_threshold}"
    )
    record = records.DecisionRecord(
        record_id=proposal_id,
        zone_id=zone,
        action="restore",
        outcome="no-change",
        previous_threshold=previous,
        new_threshold=s.baseline_threshold,
        period=period,
        rationale="operator restore to BASELINE_THRESHOLD",
    )
    pending = gate.prepare(
        "set_rate_limit_threshold",
        {"threshold": s.baseline_threshold, "demo_prefix": s.demo_prefix},
        proposal_id,
    )
    approval = _resolve_approval(pending, args)
    gate_result: dict[str, Any] | None = None
    if approval is None:
        record.outcome = "declined"
    else:
        try:
            gate_result = await gate.execute_approved(
                pending,
                approval,
                assert_target=gate.assert_demo_rule(),
                verify=gate.verify_threshold(s.baseline_threshold),
            )
            ledger.gate_statements += 1
            record.outcome = "executed"
            record.statement = pending.sql
            record.approved_by = approval.approver
            record.approval_method = approval.method
        except (gate.TargetAssertionFailed, gate.VerificationFailed, RuntimeError) as e:
            console.print(f"[red]{type(e).__name__}:[/red] {e}")
            record.outcome = f"failed: {e}"
            rc = 1
    sink = await _write_record(record, args, ledger)
    ledger.print_summary()
    path = ledger.save({"gate": gate_result, "record": record.model_dump(), "record_sink": sink})
    console.print(f"run record: {path}")
    return rc


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "setup":
            return asyncio.run(cmd_setup())
        if args.command == "validate":
            return asyncio.run(cmd_validate())
        if args.command == "run":
            return asyncio.run(cmd_run(args))
        if args.command == "restore":
            return asyncio.run(cmd_restore(args))
    except ConfigError as e:
        console.print(f"[red]config:[/red] {e}")
        return 2
    except KeyboardInterrupt:
        console.print("[yellow]interrupted[/yellow]")
        return 130
    return 0
