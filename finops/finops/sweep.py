"""The FinOps sweep.

1. The mini tier (SWEEP_MODEL) runs the query pack - eight SELECTs rendered from
   finops/queries/ - through the read-only StackQL MCP server and classifies every row into the
   shared findings shape, with the monthly estimate the SQL computed.
2. Every finding goes to the reasoning tier (REASONING_MODEL) batched per provider. It decides
   what is safe to delete now and drafts a batch of StackQL DELETE/EXEC statements ordered by
   savings, for human review only. Nothing is executed: the server is read_only and the
   mutation tools are not visible to either model.
3. Outputs: console table and brief, runs/finops-<ts>.json, runs/finops-<ts>.md, then the
   cost/trace block.
"""

from __future__ import annotations

import json

from agents import Runner, gen_trace_id, trace

from .config import PROVIDERS, configured_providers, missing_tenancy, settings
from .costs import RunLedger, console
from .findings import FindingSet, ProviderPlan, severity_for_cost
from .mcp import assert_read_only, read_only_server
from .queries import list_queries
from .report import banner, print_findings, print_plan, write_markdown_report
from .tiers import make_agent

SWEEP_INSTRUCTIONS = """You are a scheduled, read-only FinOps sweep over a demo cloud estate. You run on a
timer; nobody typed a prompt. Work through the query pack in order:

1. Run each query with run_select_query exactly as given (format json). Do not rewrite SQL and do
   not call any other tool. Zero rows means no finding for that query - move on, do not retry.
2. Turn every row into one finding in the shared schema, one finding per resource. Report every
   row: FinOps findings are individually small and add up.
3. finding_type is unattached_volume, unassociated_ip or stale_snapshot (from the query id).
   resource is the volume, disk, address or snapshot id or name. query_id is the query id.
   evidence is the query id plus the identifying values from the row (id, size, type, age,
   tags or labels, est_monthly_usd).
4. Copy est_monthly_usd into monthly_cost_estimate_usd. Severity follows it: high at 50 USD or
   more, medium at 5 USD or more, low otherwise, info when there is no estimate. The estimates
   are list-price approximations computed in SQL; call them estimates in the title.
5. proposed_remediation is the matching remediation template with the resource identifiers filled
   in (Azure: the resource group is the id path segment after /resourceGroups/; Google: the zone is
   the last segment of the zone URL). It is never executed by you.
6. Finish with a summary (2-4 sentences, matter of fact): findings per provider and the estimated
   monthly total.

Tenancy in scope (the only accounts you may query):
{tenancy}
"""

REASONING_INSTRUCTIONS = """You are the reasoning tier of a FinOps sweep. You receive every finding a smaller
model classified for one provider ({provider}). Decide which of these resources are safe to delete
now and draft the batch of StackQL statements a platform engineer would review. Nothing you return
is executed by anyone but that engineer.

Safe to delete now means all of the following hold: the resource is attached to or associated with
nothing; it carries the demo tag or label {tag_key}={tag_value}, or its name starts with {prefix},
or it carries no owner tag at all; and nothing still depends on it (defer a snapshot that is the
only copy of a volume that no longer exists, and defer anything created less than one day ago).
Everything else goes to deferred with a one sentence reason. You may run at most three read-only
SELECTs through StackQL to confirm a detail that changes a verdict; do not repeat the sweep.

Each proposed statement is one complete StackQL DELETE or EXEC statement built from the templates
below with literal identifiers (no placeholders), one per resource. Copy monthly_cost_estimate_usd
from the finding. Order proposed by monthly savings, highest first. Never propose anything outside
the tenancy.

Tenancy:
{tenancy}

Remediation templates:
{templates}
"""


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
    s = settings()
    lines = []
    if "aws" in providers:
        lines.append(f"AWS region {s.aws_region}")
    if "azure" in providers:
        lines.append(f"Azure subscription {s.azure_subscription_id}")
    if "google" in providers:
        lines.append(f"Google project {s.google_project}")
    lines.append(
        f"Demo tag/label {s.demo_tag_key}={s.demo_tag_value}; demo name prefix {s.demo_prefix}"
    )
    lines.append(f"Snapshots older than {s.snapshot_max_age_days} days count as stale")
    return "\n".join(f"- {line}" for line in lines)


def query_pack(providers: list[str]) -> str:
    """The catalogue the sweep model works from: id, description and rendered SQL for every
    SELECT whose providers are in scope, then the remediation templates."""
    out = ["## Queries", ""]
    for q in list_queries(kind="select"):
        if not all(p in providers for p in q.providers):
            continue
        out.append(f"### {q.id}\n{q.description}\n```sql\n{q.render()}\n```\n")
    out.append(remediation_templates(providers))
    return "\n".join(out)


def remediation_templates(providers: list[str]) -> str:
    out = ["## Remediation templates (fill the <placeholders>; never executed here)", ""]
    for q in list_queries(kind="mutation"):
        if not all(p in providers for p in q.providers):
            continue
        out.append(f"### {q.id}\n{q.description}\n```sql\n{q.render_partial()}\n```\n")
    return "\n".join(out)


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
        "always-on, read-only: SELECTs against cloud control planes -> findings -> proposed "
        "deletions for review, cost in frame",
    )
    console.print(
        f"providers: {', '.join(providers)}  |  sweep={s.sweep.model}  reasoning={s.reasoning.model}"
    )
    ledger = RunLedger("finops", trace_id=gen_trace_id())
    pack = query_pack(providers)
    plans: list[ProviderPlan] = []
    server = read_only_server("stackql-finops")
    assert_read_only(server)
    async with server:
        with trace("finops sweep", trace_id=ledger.trace_id):
            sweeper = make_agent(
                "sweep",
                name="finops-sweep",
                instructions=SWEEP_INSTRUCTIONS.format(tenancy=tenancy_block(providers)),
                mcp_servers=[server],
                output_type=FindingSet,
            )
            result = await Runner.run(sweeper, f"Query pack:\n\n{pack}", max_turns=40)
            ledger.record("sweep (classify)", s.sweep.model, result)
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
                        instructions=REASONING_INSTRUCTIONS.format(
                            provider=provider,
                            tag_key=s.demo_tag_key,
                            tag_value=s.demo_tag_value,
                            prefix=s.demo_prefix,
                            tenancy=tenancy_block([provider]),
                            templates=remediation_templates([provider]),
                        ),
                        mcp_servers=[server],
                        output_type=ProviderPlan,
                    )
                    payload = json.dumps(
                        [{"fingerprint": f.fingerprint, **f.model_dump()} for f in group],
                        indent=1,
                        default=str,
                    )
                    r2 = await Runner.run(
                        reasoner, f"Findings for {provider} (JSON):\n{payload}", max_turns=12
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
