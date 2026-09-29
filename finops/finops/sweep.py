"""The FinOps sweep.

1. The mini tier (SWEEP_MODEL) works from the intent in finops/prompts/sweep.md: unattached
   volumes and disks, unassociated addresses and stale snapshots for every provider in scope. It
   discovers the resources through the StackQL query library and the list/describe tools on the
   read-only MCP server, runs the SELECTs it validated, classifies every row into the shared
   findings shape and attaches a list-price monthly estimate, labelled as an estimate.
2. Every finding goes to the reasoning tier (REASONING_MODEL), batched per provider, with
   finops/prompts/reasoning.md: decide what is safe to delete now, discover the delete or
   release contract with list_methods / describe_method, and draft one StackQL statement per
   resource ordered by savings, for human review only. Nothing is executed: the server is
   read_only and the mutation tools are not visible to either model.
3. Outputs: console table and brief, runs/finops-<ts>.json, runs/finops-<ts>.md, then the
   cost/trace block.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from agents import Runner, gen_trace_id, trace

from .config import PROVIDERS, configured_providers, missing_tenancy, settings
from .costs import RunLedger, console
from .findings import FindingSet, ProviderPlan, severity_for_cost
from .mcp import assert_read_only, read_only_server
from .prompts import discovery_briefing, render_prompt
from .report import banner, print_findings, print_plan, write_markdown_report
from .tiers import make_agent

SWEEP_MAX_TURNS = 60  # discovery calls per provider plus the SELECTs themselves
REASONING_MAX_TURNS = 20


def select_providers(requested: list[str] | None) -> list[str]:
    """Providers to sweep: the ones named with --providers, else every provider whose
    credentials are configured. Tenancy pins (region, subscription, project) must be set."""
    if requested:
        unknown = [p for p in requested if p not in PROVIDERS]
        if unknown:
            raise SystemExit(f"unknown provider(s) {unknown}; choose from {list(PROVIDERS)}")
        chosen = list(dict.fromkeys(requested))
    else:
        chosen = configured_providers()
        skipped = [p for p in PROVIDERS if p not in chosen]
        if skipped:
            console.print(f"skipping {skipped}: credentials not configured (see .env.example)")
    for p in chosen:
        miss = missing_tenancy(p)
        if miss:
            raise SystemExit(f"provider {p}: set {miss} in .env")
    if not chosen:
        raise SystemExit("no provider configured - set credentials in .env or pass --providers")
    return chosen


def tenancy_block(providers: list[str]) -> str:
    """The `{{ tenancy }}` placeholder: one line per provider in scope with the pin the
    provider's SELECT methods require, as a value the model puts in WHERE."""
    s = settings()
    lines = []
    if "aws" in providers:
        lines.append(f"aws: region {s.aws_region}")
    if "azure" in providers:
        lines.append(f"azure: subscription {s.azure_subscription_id}")
    if "google" in providers:
        lines.append(f"google: project {s.google_project}")
    return "\n".join(f"- {line}" for line in lines)


def sweep_trigger(providers: list[str]) -> str:
    """The run input: what a scheduler hands the agent. No SQL, no resource names."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    return (
        f"Scheduled sweep triggered at {now}. Providers in scope: {', '.join(providers)}. "
        "Discover the resources, run the checks and return the findings."
    )


def normalise(fs: FindingSet) -> FindingSet:
    """Re-apply the deterministic severity rule where an estimate exists and drop findings
    for providers not in the shared provider list (a model cannot widen the scope)."""
    kept = []
    for f in fs.findings:
        if f.provider not in PROVIDERS:
            continue
        if f.monthly_cost_estimate_usd is not None:
            f.severity = severity_for_cost(f.monthly_cost_estimate_usd)
        kept.append(f)
    fs.findings = kept
    return fs


async def run_sweep(providers: list[str], *, skip_reasoning: bool = False) -> int:
    s = settings()
    banner(
        "FinOps sweep",
        "always-on, read-only: intent prompt -> discovery through StackQL -> SELECTs against "
        "cloud control planes -> findings -> proposed deletions for review, cost in frame",
    )
    console.print(
        f"providers: {', '.join(providers)}  |  sweep={s.sweep.model}  reasoning={s.reasoning.model}"
    )
    ledger = RunLedger("finops", trace_id=gen_trace_id())
    plans: list[ProviderPlan] = []
    server = read_only_server("stackql-finops")
    assert_read_only(server)
    async with server:
        briefing = await discovery_briefing(server, notes=ledger.notes)
        for n in ledger.notes:
            console.print(f"[yellow]{n}[/yellow]")
        with trace("finops sweep", trace_id=ledger.trace_id):
            sweeper = make_agent(
                "sweep",
                name="finops-sweep",
                instructions=render_prompt(
                    "sweep", providers=", ".join(providers), tenancy=tenancy_block(providers)
                ),
                briefing=briefing,
                mcp_servers=[server],
                output_type=FindingSet,
            )
            result = await Runner.run(sweeper, sweep_trigger(providers), max_turns=SWEEP_MAX_TURNS)
            ledger.record("sweep (discover + classify)", s.sweep.model, result)
            fs = normalise(result.final_output)
            print_findings(fs)

            if skip_reasoning:
                ledger.notes.append("reasoning tier skipped (--skip-reasoning)")
            elif not fs.findings:
                ledger.notes.append("no findings: reasoning tier not invoked")
            else:
                for provider, group in fs.by_provider().items():
                    console.print(
                        f"escalating {len(group)} {provider} finding(s) to {s.reasoning.model}"
                    )
                    reasoner = make_agent(
                        "reasoning",
                        name=f"finops-reasoning-{provider}",
                        instructions=render_prompt(
                            "reasoning", provider=provider, tenancy=tenancy_block([provider])
                        ),
                        briefing=briefing,
                        mcp_servers=[server],
                        output_type=ProviderPlan,
                    )
                    payload = json.dumps(
                        [{"fingerprint": f.fingerprint, **f.model_dump()} for f in group],
                        indent=1,
                        default=str,
                    )
                    r2 = await Runner.run(
                        reasoner,
                        f"Findings for {provider} (JSON):\n{payload}",
                        max_turns=REASONING_MAX_TURNS,
                    )
                    ledger.record(f"reasoning ({provider})", s.reasoning.model, r2)
                    plan: ProviderPlan = r2.final_output
                    plan.provider = provider
                    plans.append(plan)
                    print_plan(plan)

    report = write_markdown_report(fs, plans, ledger, providers)
    console.rule("outputs")
    console.print(f"cost report: {report}")
    ledger.print_summary()
    json_path = ledger.save(
        {
            "providers": providers,
            "findings": fs.model_dump(),
            "plans": [p.model_dump() for p in plans],
            "report_md": str(report),
        }
    )
    console.print(f"run record: {json_path}")
    return 0
