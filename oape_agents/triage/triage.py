"""Gated incident triage - the closed loop and the one live mutation.

    uv run python -m oape_agents.triage.triage [--approve] [--decline] [--fallback] [--record]
    uv run python -m oape_agents.triage.triage --reset      # put the ASG back to desired=1 (no model)

Trigger  : runs/alert.json from oape_agents.triage.alert (fired automatically if absent)
Diagnose : the reasoning-tier agent investigates with SELECTs only over the read-only server
Propose  : a structured proposal naming one action from a fixed menu (scale out by one)
Gate     : the operator types the approval phrase in the terminal (or --approve for rehearsal)
Execute  : oape_agents.triage.gate - tag assertion, then the single statement via run_mutation_query
Verify   : poll the verification SELECT until in-service instances match the new desired capacity
Close    : the agent writes the incident note; cost and trace are printed
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid

from agents import Runner, gen_trace_id, trace

from oape_agents.common.config import settings
from oape_agents.common.costs import RunLedger, console
from oape_agents.common.mcp import read_only_server
from oape_agents.common.queries import load_query, stackql_exec
from oape_agents.common.runlog import banner, load_fallback, save_fallback
from oape_agents.common.tiers import make_agent
from oape_agents.sweeps.base import render_query
from oape_agents.triage import alert as alert_mod
from oape_agents.triage import gate
from oape_agents.triage.models import Closeout, Diagnosis, Proposal

DIAGNOSE_INSTRUCTIONS = """You are the on-call SRE agent for a demo estate. An alert fired; nobody
typed a prompt. Investigate with SELECT statements only, through the canonical queries:

- triage/aws_asg_state          the auto scaling group and its member instances
- triage/aws_target_groups      the checkout target group (take target_group_arn from it)
- triage/aws_target_health      target health for that ARN (pass target_group_arn to render_query)
- triage/aws_service_instances  instances tagged for the service
- triage/github_recent_deployments  deployments in the last {hours} hours

Run each once via run_select_query with the SQL from the pack or render_query; do not rewrite SQL.
Then produce the diagnosis: capacity actually in service versus desired, target health, any recent
deployment that correlates, and the most likely cause. Be specific with ids and numbers.

Tenancy: AWS account {account}, region {region}. Only this account is in scope."""

PROPOSE_INSTRUCTIONS = """You are proposing remediation for the diagnosis below. The menu is fixed:

- scale_out_by_one : raise the ASG desired capacity by exactly one (never beyond max_size)
- no_action        : when the evidence does not support a capacity change

Pick one. Name the exact target, the new desired capacity, the blast radius, the rollback (the
same StackQL statement - UPDATE aws.autoscaling.auto_scaling_groups SET desired_capacity = <old>
WHERE region = ... AND auto_scaling_group_name = ... - never a CLI command) and what the
verification SELECT must show. You do not execute anything: a human approves the statement in
the terminal."""

CLOSE_INSTRUCTIONS = """Write the incident close-out note from the facts below: what the alert said,
what the diagnosis found, what was approved and executed (or declined), and what the verification
SELECT showed. One paragraph, matter of fact, for the on-call handover."""


def verify_rows(as_of: str = "") -> list[dict]:
    return stackql_exec(load_query("triage/verify_asg_capacity").render())


def in_service_count(rows: list[dict]) -> tuple[int, int]:
    desired = int(rows[0]["desired_capacity"]) if rows else 0
    return desired, sum(1 for r in rows if r.get("lifecycle_state") == "InService")


async def poll_recovery(desired: int, timeout_s: int = 240) -> tuple[bool, list[dict]]:
    console.rule("verify")
    start = time.time()
    rows: list[dict] = []
    while time.time() - start < timeout_s:
        rows = verify_rows()
        d, n = in_service_count(rows)
        console.print(f"  desired={d} in_service={n} ({int(time.time() - start)}s)")
        if d == desired and n >= desired:
            return True, rows
        await asyncio.sleep(15)
    return False, rows


async def run(approve_flag: bool, decline_flag: bool, record: bool) -> int:
    s = settings()
    banner(
        "gated incident triage",
        "diagnose with SELECTs -> propose -> human approval -> one logged mutation -> verify",
    )
    alert = alert_mod.load() or alert_mod.fire()
    console.print(f"alert {alert.alert_id} ({alert.source}): {alert.symptom}")
    console.print(f"signal: {alert.signal}")
    ledger = RunLedger("triage", trace_id=gen_trace_id())
    payload: dict = {"alert": alert.model_dump()}

    async with read_only_server("stackql-triage-ro") as ro:
        pack = "\n\n".join(
            f"-- {qid}\n{load_query(qid).render()}"
            for qid in (
                "triage/aws_asg_state",
                "triage/aws_target_groups",
                "triage/aws_service_instances",
                "triage/github_recent_deployments",
            )
        )
        with trace("oape triage", trace_id=ledger.trace_id):
            diagnoser = make_agent(
                "reasoning",
                name="triage-diagnose",
                instructions=DIAGNOSE_INSTRUCTIONS.format(
                    hours=s.tenancy["recent_deploy_hours"],
                    account=s.aws_account_id,
                    region=s.aws_region,
                ),
                mcp_servers=[ro],
                tools=[render_query],
                output_type=Diagnosis,
            )
            r1 = await Runner.run(
                diagnoser,
                f"Alert:\n{alert.model_dump_json(indent=2)}\n\nQuery pack:\n{pack}",
                max_turns=25,
            )
            ledger.record("diagnose (SELECT only)", s.reasoning.model, r1)
            diag: Diagnosis = r1.final_output
            console.rule("diagnosis")
            console.print(diag.summary)
            console.print(f"capacity: {diag.capacity.model_dump()}")
            for line in diag.recent_deployments:
                console.print(f"  deployment: {line}")
            console.print(f"hypothesis: {diag.hypothesis}")
            payload["diagnosis"] = diag.model_dump()

            proposer = make_agent(
                "reasoning",
                name="triage-propose",
                instructions=PROPOSE_INSTRUCTIONS,
                output_type=Proposal,
                include_dialect_notes=False,
            )
            r2 = await Runner.run(
                proposer,
                f"Diagnosis:\n{diag.model_dump_json(indent=2)}\n\nASG name: {s.tenancy['checkout_asg_name']}",
                max_turns=3,
            )
            ledger.record("propose", s.reasoning.model, r2)
            prop: Proposal = r2.final_output
            payload["proposal"] = prop.model_dump()
            console.rule("proposal")
            console.print(
                f"action: {prop.action}  target: {prop.target}  desired_capacity: {prop.desired_capacity}"
            )
            console.print(f"rationale: {prop.rationale}")
            console.print(f"blast radius: {prop.blast_radius}")
            console.print(f"rollback: {prop.rollback}")

            outcome = "no_action"
            execution: dict | None = None
            recovered = False
            verify: list[dict] = []
            if prop.action == "scale_out_by_one":
                if (
                    prop.desired_capacity > diag.capacity.max_size
                    or prop.desired_capacity != diag.capacity.desired_capacity + 1
                ):
                    console.print(
                        "[red]proposal outside the menu bounds: treated as no_action[/red]"
                    )
                else:
                    proposal_id = f"prop-{uuid.uuid4().hex[:6]}"
                    pending = gate.prepare(
                        prop.action, {"desired_capacity": str(prop.desired_capacity)}, proposal_id
                    )
                    if decline_flag:
                        console.print("[yellow]--decline given: declining at the gate[/yellow]")
                        approval = None
                    elif approve_flag:
                        approval = gate.rehearsal_approval(pending)
                    else:
                        approval = gate.ask_terminal(pending)
                    payload["pending"] = {
                        "proposal_id": pending.proposal_id,
                        "query_id": pending.query_id,
                        "sql": pending.sql,
                    }
                    if approval is None:
                        outcome = "declined"
                        ledger.notes.append(
                            "gate declined: no mutation executed (0 mutation calls)"
                        )
                    else:
                        execution = await gate.execute_approved(pending, approval)
                        ledger.entries.append(_mutation_entry())
                        payload["execution"] = execution
                        recovered, verify = await poll_recovery(prop.desired_capacity)
                        outcome = "recovered" if recovered else "not_recovered"
            payload["verification"] = verify
            closer = make_agent(
                "sweep",
                name="triage-close",
                instructions=CLOSE_INSTRUCTIONS,
                output_type=Closeout,
                include_dialect_notes=False,
            )
            facts = {
                "alert": alert.model_dump(),
                "diagnosis": diag.model_dump(),
                "proposal": prop.model_dump(),
                "execution": execution,
                "verification": verify,
                "outcome": outcome,
            }
            r3 = await Runner.run(closer, json.dumps(facts, default=str), max_turns=3)
            ledger.record("close-out note", s.sweep.model, r3)
            close: Closeout = r3.final_output
            payload["closeout"] = close.model_dump()
            console.rule("close-out")
            console.print(f"[bold]{close.outcome}[/bold]: {close.summary}")
    ledger.print_summary()
    payload["ledger"] = ledger.to_dict()
    ledger.save(payload)
    if record:
        console.print(f"recorded fallback: {save_fallback('triage', payload)}")
    return 0


def _mutation_entry():
    from oape_agents.common.costs import RunEntry

    return RunEntry(
        label="execute (approved)",
        model="-",
        requests=0,
        tool_calls=2,
        select_calls=1,
        mutation_calls=1,
    )


def replay() -> int:
    data = load_fallback("triage")
    banner("gated incident triage (replayed)", f"recorded {data.get('recorded_at')}")
    a = data["alert"]
    console.print(f"alert {a['alert_id']}: {a['symptom']}")
    d = data["diagnosis"]
    console.rule("diagnosis")
    console.print(d["summary"])
    console.print(f"capacity: {d['capacity']}")
    console.print(f"hypothesis: {d['hypothesis']}")
    p = data["proposal"]
    console.rule("proposal")
    console.print(
        f"action: {p['action']}  target: {p['target']}  desired_capacity: {p['desired_capacity']}"
    )
    if data.get("pending"):
        console.rule("[bold red]approval gate[/bold red]")
        console.print(f"[bold]{data['pending']['sql']}[/bold]")
    if data.get("execution"):
        console.print(f"[green]executed[/green]: {data['execution'].get('server_response', '')}")
    console.rule("verify")
    for r in data.get("verification", []):
        console.print(f"  {r}")
    console.rule("close-out")
    console.print(f"[bold]{data['closeout']['outcome']}[/bold]: {data['closeout']['summary']}")
    ledger = RunLedger("triage", trace_id=data.get("ledger", {}).get("trace_id"))
    for e in data.get("ledger", {}).get("entries", []):
        ledger.record_dict(e)
    ledger.print_summary(replay=True)
    return 0


def reset() -> int:
    from seed.aws.seed_aws import scale_checkout

    scale_checkout(1)
    console.print("checkout ASG desired capacity reset to 1 (operator tooling, not the agent path)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--approve", action="store_true", help="rehearsal: approve at the gate without the prompt"
    )
    ap.add_argument(
        "--decline", action="store_true", help="decline at the gate (proves the abort path)"
    )
    ap.add_argument("--fallback", action="store_true")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    if a.reset:
        return reset()
    if a.fallback:
        return replay()
    return asyncio.run(run(a.approve, a.decline, a.record))


if __name__ == "__main__":
    sys.exit(main())
