"""The shared sweep pattern.

1. A mini-tier agent runs the scenario's canonical queries through the read-only StackQL MCP
   server and classifies the rows into the shared findings schema. SQL comes from queries/ via the
   render_query tool; the model supplies parameter values only (fan-out lists from inventory rows).
2. Findings at or above ESCALATION_SEVERITY go to the frontier-tier agent, which judges materiality,
   correlates findings, and drafts a remediation plan and the StackQL statement a human would review.
3. Artifacts: one GitHub issue per material finding (cspm, finops) or a recertification report
   (entitlements), plus a console brief.
4. The run ends with the cost and trace summary. `--fallback` replays a recorded run from
   fallbacks/<scenario>.json; `--record` saves the live run as the new fallback.

Nothing here can mutate: the MCP server is started in read_only mode and the mutation tools are
hidden from the model (see oape_agents/common/mcp.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime

from agents import Runner, function_tool, gen_trace_id, trace

from oape_agents.common import github_out
from oape_agents.common.config import RUNS_DIR, provider_configured, settings
from oape_agents.common.costs import RunLedger, console
from oape_agents.common.findings import (
    SEVERITY_ORDER,
    Assessment,
    AssessmentSet,
    Finding,
    FindingSet,
    Severity,
    escalation_candidates,
)
from oape_agents.common.mcp import read_only_server
from oape_agents.common.queries import list_queries, load_query, render_union, sql_list
from oape_agents.common.runlog import banner, load_fallback, print_findings, save_fallback
from oape_agents.common.tiers import make_agent

LIST_PARAMS = {"s3_bucket_list", "aws_region_list", "github_repo_list"}
UNION_PARAMS = {"iam_user_name", "gcs_bucket_name", "project_id", "user_name"}


@dataclass
class SweepSpec:
    scenario: str
    title: str
    query_ids: list[str]
    classify_instructions: str
    escalate_instructions: str
    artifact: str = "issues"  # issues | report
    context_notes: list[str] = field(default_factory=list)
    # "each": one assessment per escalated finding, one issue per material finding
    # "batch": every finding goes to the reasoning tier, one assessment and one issue per provider
    escalation_mode: str = "each"


def _tenancy_block() -> str:
    s = settings()
    lines = [
        f"AWS account {s.aws_account_id}, region {s.aws_region} (IAM signs against us-east-1)",
        f"Azure subscription {s.azure_subscription_id}, location {s.azure_location}",
        f"Google project {s.google_project}",
        f"GitHub org {s.github_org}",
        f"Demo tag {s.demo_tag_key}={s.demo_tag_value}; demo name prefix {s.demo_prefix}",
    ]
    return "\n".join(f"- {line}" for line in lines)


def query_pack(spec: SweepSpec) -> str:
    """The catalogue the model works from: id, description, parameters, and rendered SQL where every
    parameter is static. Queries with fan-out parameters are rendered on demand via render_query."""
    out = []
    for qid in spec.query_ids:
        q = load_query(qid)
        if not all(provider_configured(p) for p in q.providers):
            out.append(f"### {qid}\n(skipped: credentials for {q.providers} are not configured)\n")
            continue
        tenancy = settings().tenancy
        dynamic = [
            p
            for p in q.params
            if p in LIST_PARAMS
            or p in UNION_PARAMS
            or p == "aws_admins_sql"
            or tenancy.get(p, "") == ""
        ]
        if dynamic:
            out.append(
                f"### {qid}\n{q.description}\nDynamic parameters: {dynamic}. Call render_query with a "
                f"JSON object for these parameters to obtain the SQL, then run it verbatim.\n"
            )
        else:
            out.append(f"### {qid}\n{q.description}\n```sql\n{q.render()}\n```\n")
    return "\n".join(out)


@function_tool
def render_query(query_id: str, params_json: str = "{}") -> str:
    """Render a canonical query from the library with parameter values and return the SQL to run
    verbatim with run_select_query. List parameters (s3_bucket_list, aws_region_list,
    github_repo_list) take a JSON array of strings. Per-key parameters (iam_user_name,
    gcs_bucket_name, project_id) take a JSON array too: one SELECT per value is rendered and
    UNION ALL-ed. aws_admin_users takes a JSON array of IAM user names that hold AdministratorAccess."""
    params = json.loads(params_json or "{}")
    q = load_query(query_id)
    overrides: dict[str, str] = {}
    union_param: tuple[str, list[str]] | None = None
    for k, v in params.items():
        if k == "aws_admin_users":
            values = [str(x) for x in (v if isinstance(v, list) else [v])]
            overrides["aws_admins_sql"] = render_union(
                "entitlements/aws_admin_user_literal", "user_name", values
            )
        elif k in LIST_PARAMS:
            values = [str(x) for x in (v if isinstance(v, list) else [v])]
            overrides[k] = sql_list(values)
        elif k in UNION_PARAMS and isinstance(v, list):
            union_param = (k, [str(x) for x in v])
        else:
            overrides[k] = str(v)
    if union_param:
        return render_union(query_id, union_param[0], union_param[1], **overrides)
    return q.render(**overrides)


@function_tool
def list_scenario_queries(scenario: str) -> str:
    """List the canonical query ids, descriptions and parameters for a scenario."""
    return "\n".join(
        f"{q.id}: {q.description} (params: {', '.join(q.params) or 'none'})"
        for q in list_queries(scenario)
    )


CLASSIFY_BASE = """You are a scheduled, read-only {title} sweep over a demo cloud estate. You run
on a timer; nobody typed a prompt. Work through the query pack below in order:

1. For each query, obtain the SQL (given inline, or via render_query for fan-out parameters -
   take the parameter values from the inventory query results earlier in the pack) and run it with
   run_select_query exactly as rendered. Do not rewrite SQL. Do not call any other tool.
2. Turn rows into findings using the shared schema. One finding per affected resource. Rows that
   carry a classification column (open_to_world, from_internet, status) are findings only when
   that column says so. Zero rows means no finding for that query - move on, do not retry.
3. Evidence is the query id plus the identifying values from the row (ids, names, ports, sources,
   ages, amounts). proposed_remediation is the StackQL statement or configuration change that
   would fix it (UPDATE/DELETE/EXEC ... or a console step) - it is never executed by you.
4. Severity: {severity_guide}
5. Finish with a short summary (2-4 sentences, matter of fact).

Tenancy in scope (the only accounts you may query):
{tenancy}
{notes}
"""

ESCALATE_BASE = """You are the reasoning tier for a {title} sweep. You receive findings a smaller
model classified at or above the escalation threshold. For each one decide whether it is material
now, correlate it with the other findings where that changes the answer, and draft a remediation
plan a platform engineer would follow. You may run at most three read-only SELECTs through StackQL
to confirm a detail that changes a verdict; do not repeat the sweep. Keep each rationale to two or
three sentences. Never propose anything outside the tenancy below.
{extra}
Return one assessment per finding fingerprint you were given, plus a one-paragraph executive summary.

Tenancy:
{tenancy}
"""


def _findings_block(findings: list[Finding]) -> str:
    return json.dumps(
        [{"fingerprint": f.fingerprint, **f.model_dump()} for f in findings], indent=1, default=str
    )


async def run_live(spec: SweepSpec, ledger: RunLedger, dry_run: bool) -> dict:
    s = settings()
    pack = query_pack(spec)
    async with read_only_server(f"stackql-{spec.scenario}") as server:
        sweeper = make_agent(
            "sweep",
            name=f"{spec.scenario}-sweep",
            instructions=CLASSIFY_BASE.format(
                title=spec.title,
                severity_guide=spec.classify_instructions,
                tenancy=_tenancy_block(),
                notes="\n".join(f"Note: {n}" for n in spec.context_notes),
            ),
            mcp_servers=[server],
            tools=[render_query, list_scenario_queries],
            output_type=FindingSet,
        )
        with trace(f"oape {spec.scenario} sweep", trace_id=ledger.trace_id):
            result = await Runner.run(
                sweeper, f"Scenario: {spec.scenario}\n\nQuery pack:\n\n{pack}", max_turns=60
            )
            ledger.record("sweep (classify)", s.sweep.model, result)
            fs: FindingSet = result.final_output
            fs.scenario = spec.scenario
            print_findings(fs)

            if spec.escalation_mode == "batch":
                candidates = list(fs.findings)
                extra_note = (
                    "\n\nBatch mode: return ONE assessment per provider (aws, azure, google), using the "
                    "provider name as finding_fingerprint, covering every finding for that provider."
                )
            else:
                candidates = escalation_candidates(fs, s.escalation_severity)
                extra_note = ""
            assessments: list[Assessment] = []
            exec_summary = ""
            if candidates:
                console.print(
                    f"escalating {len(candidates)} finding(s) "
                    f"({'all, batched by provider' if spec.escalation_mode == 'batch' else 'at or above ' + repr(s.escalation_severity)}) "
                    f"to {s.reasoning.model}"
                )
                reasoner = make_agent(
                    "reasoning",
                    name=f"{spec.scenario}-reasoning",
                    instructions=ESCALATE_BASE.format(
                        title=spec.title,
                        extra=spec.escalate_instructions + extra_note,
                        tenancy=_tenancy_block(),
                    ),
                    mcp_servers=[server],
                    output_type=AssessmentSet,
                )
                r2 = await Runner.run(
                    reasoner,
                    "Escalated findings (JSON):\n"
                    + _findings_block(candidates)
                    + "\n\nAll findings from this sweep, for correlation:\n"
                    + _findings_block(fs.findings),
                    max_turns=25,
                )
                ledger.record("reasoning (assess)", s.reasoning.model, r2)
                aset: AssessmentSet = r2.final_output
                assessments = aset.assessments
                exec_summary = aset.executive_summary
                console.rule("assessment")
                for a in assessments:
                    console.print(
                        f"[{'red' if a.material else 'yellow'}]{a.finding_fingerprint}[/] material={a.material}: {a.rationale[:300]}"
                    )
                console.print(exec_summary)
    artifacts = emit_artifacts(spec, fs, assessments, exec_summary, dry_run)
    return {
        "findings": fs.model_dump(),
        "assessments": [a.model_dump() for a in assessments],
        "executive_summary": exec_summary,
        "artifacts": artifacts,
    }


def emit_artifacts(
    spec: SweepSpec, fs: FindingSet, assessments: list[Assessment], summary: str, dry_run: bool
) -> list[dict]:
    by_fp = {a.finding_fingerprint: a for a in assessments}
    out: list[dict] = []
    if not fs.findings:
        console.print("no findings: no artifacts created")
        return out
    if spec.artifact == "issues" and spec.escalation_mode == "batch":
        console.rule("github issues (one per provider)")
        for provider in sorted({f.provider for f in fs.findings}):
            group = [f for f in fs.findings if f.provider == provider]
            total = sum(f.monthly_cost_estimate_usd or 0.0 for f in group)
            sev = Severity.high if total >= 50 else Severity.medium if total >= 5 else Severity.low
            batched = Finding(
                provider=provider,
                resource=f"{provider}: {len(group)} idle or orphaned resources",
                finding_type="idle_resources_batch",
                severity=sev,
                title=f"{len(group)} idle resources in {provider}, about USD {total:,.2f} per month",
                evidence="\n".join(
                    f"- [{f.finding_type}] {f.resource}: USD {f.monthly_cost_estimate_usd or 0:,.2f}/month"
                    for f in sorted(group, key=lambda x: -(x.monthly_cost_estimate_usd or 0))
                ),
                proposed_remediation="\n".join(
                    f"-- {f.resource}\n{f.proposed_remediation}" for f in group
                ),
                query_id="finops",
                monthly_cost_estimate_usd=round(total, 2),
            )
            try:
                res = github_out.open_issue(
                    batched, by_fp.get(provider), spec.scenario, dry_run=dry_run
                )
            except Exception as e:  # noqa: BLE001
                res = {"action": "error", "error": str(e)[:200]}
            res["fingerprint"] = batched.fingerprint
            res["title"] = batched.title
            out.append(res)
            console.print(f"  {res['action']}: {batched.title} {res.get('url', '')}")
        return out
    if spec.artifact == "issues":
        s = settings()
        material = [
            f
            for f in fs.findings
            if (
                by_fp.get(f.fingerprint).material
                if f.fingerprint in by_fp
                else f.severity.at_least(s.escalation_severity)
            )
        ]
        console.rule("github issues")
        for f in material:
            try:
                res = github_out.open_issue(
                    f, by_fp.get(f.fingerprint), spec.scenario, dry_run=dry_run
                )
            except Exception as e:  # noqa: BLE001
                res = {"action": "error", "error": str(e)[:200]}
            res["fingerprint"] = f.fingerprint
            res["title"] = f.title
            out.append(res)
            console.print(f"  {res['action']}: {f.title} {res.get('url', '')}")
        skipped = len(fs.findings) - len(material)
        if skipped:
            console.print(
                f"  {skipped} finding(s) below the issue threshold stay in the brief only"
            )
    else:
        path = write_recert_report(spec, fs, assessments, summary)
        out.append({"action": "report", "path": str(path)})
        console.print(f"recertification report: {path}")
    return out


def write_recert_report(
    spec: SweepSpec, fs: FindingSet, assessments: list[Assessment], summary: str
):
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = RUNS_DIR / f"{spec.scenario}-recertification-{ts}.md"
    by_fp = {a.finding_fingerprint: a for a in assessments}
    lines = [
        f"# {spec.title} recertification - {ts}",
        "",
        "Generated by a scheduled read-only sweep. Every line is backed by a SELECT against the",
        "control plane; nothing was changed. Owners confirm or revoke each entitlement below.",
        "",
        "## Summary",
        "",
        fs.summary,
        "",
    ]
    if summary:
        lines += ["## Reasoning tier assessment", "", summary, ""]
    lines += [
        "## Entitlements requiring a decision",
        "",
        "| sev | provider | principal / resource | finding | decision |",
        "|---|---|---|---|---|",
    ]
    for f in sorted(fs.findings, key=lambda x: -SEVERITY_ORDER.index(Severity(x.severity).value)):
        lines.append(
            f"| {f.severity.value} | {f.provider} | `{f.resource}` | {f.title} | confirm / revoke |"
        )
    lines += ["", "## Evidence and proposed remediation", ""]
    for f in fs.findings:
        lines += [
            f"### {f.title}",
            "",
            f"- provider: {f.provider}",
            f"- resource: `{f.resource}`",
            f"- query: `queries/{f.query_id}.sql`",
            "",
            "```",
            f.evidence,
            "```",
            "",
            "Proposed remediation (for review, not executed):",
            "",
            "```sql",
            f.proposed_remediation,
            "```",
            "",
        ]
        a = by_fp.get(f.fingerprint)
        if a:
            lines += [
                f"Assessment: material={a.material}. {a.rationale}",
                "",
                a.remediation_plan,
                "",
            ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def replay(spec: SweepSpec, ledger: RunLedger) -> dict:
    data = load_fallback(spec.scenario)
    console.print(f"[yellow]replaying recorded run from {data.get('recorded_at')}[/yellow]")
    fs = FindingSet.model_validate(data["findings"])
    print_findings(fs)
    assessments = [Assessment.model_validate(a) for a in data.get("assessments", [])]
    if assessments:
        console.rule("assessment (recorded)")
        for a in assessments:
            console.print(
                f"[{'red' if a.material else 'yellow'}]{a.finding_fingerprint}[/] material={a.material}: {a.rationale[:300]}"
            )
        console.print(data.get("executive_summary", ""))
    console.rule("artifacts (recorded)")
    for art in data.get("artifacts", []):
        console.print(
            f"  {art.get('action')}: {art.get('title', art.get('path', ''))} {art.get('url', '')}"
        )
    for e in data.get("ledger", {}).get("entries", []):
        ledger.record_dict(e)
    ledger.trace_id = data.get("ledger", {}).get("trace_id")
    return data


async def run_sweep(spec: SweepSpec, *, fallback: bool, dry_run: bool, record: bool) -> int:
    banner(
        spec.title,
        "always-on, read-only: SELECTs against control planes, findings -> artifacts, cost in frame",
    )
    ledger = RunLedger(spec.scenario, trace_id=None if fallback else gen_trace_id())
    if fallback:
        replay(spec, ledger)
        ledger.print_summary(replay=True)
        return 0
    payload = await run_live(spec, ledger, dry_run)
    ledger.print_summary()
    payload["ledger"] = ledger.to_dict()
    ledger.save(payload)
    if record:
        p = save_fallback(spec.scenario, payload)
        console.print(f"recorded fallback: {p}")
    return 0


def cli(spec: SweepSpec) -> int:
    ap = argparse.ArgumentParser(description=spec.title)
    ap.add_argument(
        "--fallback", action="store_true", help="replay fallbacks/<scenario>.json, no live APIs"
    )
    ap.add_argument("--dry-run", action="store_true", help="do not open GitHub issues")
    ap.add_argument("--record", action="store_true", help="save this live run as the fallback")
    a = ap.parse_args()
    return asyncio.run(run_sweep(spec, fallback=a.fallback, dry_run=a.dry_run, record=a.record))


if __name__ == "__main__":
    sys.exit(0)
