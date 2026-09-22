"""GitHub artifacts: one issue per material finding, idempotent on the finding fingerprint.
Uses the StackQL github PAT (STACKQL_GITHUB_PASSWORD) via the REST API."""

from __future__ import annotations

import os

import httpx

from .config import env, settings
from .findings import Assessment, Finding

API = "https://api.github.com"
MARK = "oape-fingerprint"


def _headers() -> dict[str, str]:
    token = env("STACKQL_GITHUB_PASSWORD", required=True)
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _repo() -> str:
    repo = settings().github_issues_repo
    if "/" not in repo:
        raise RuntimeError("GITHUB_ISSUES_REPO must be owner/repo")
    return repo


def list_open_demo_issues(scenario: str | None = None) -> list[dict]:
    q = f"repo:{_repo()} is:issue is:open label:oape-demo" + (
        f" label:{scenario}" if scenario else ""
    )
    r = httpx.get(
        f"{API}/search/issues", params={"q": q, "per_page": 100}, headers=_headers(), timeout=30
    )
    r.raise_for_status()
    return r.json().get("items", [])


def find_open_issue(fingerprint: str) -> dict | None:
    q = f'repo:{_repo()} is:issue is:open "{MARK}:{fingerprint}" in:body'
    r = httpx.get(f"{API}/search/issues", params={"q": q}, headers=_headers(), timeout=30)
    r.raise_for_status()
    items = r.json().get("items", [])
    return items[0] if items else None


def issue_body(f: Finding, a: Assessment | None, scenario: str) -> str:
    lines = [
        f"**Scenario:** {scenario}  ",
        f"**Provider:** {f.provider}  ",
        f"**Resource:** `{f.resource}`  ",
        f"**Finding type:** `{f.finding_type}`  ",
        f"**Severity:** {f.severity.value}  ",
    ]
    if f.monthly_cost_estimate_usd is not None:
        lines.append(f"**Estimated monthly waste:** USD {f.monthly_cost_estimate_usd:,.2f}  ")
    lines += ["", "## Evidence", "", "```", f.evidence, "```", ""]
    if f.query_id:
        lines.append(f"Detection query: `queries/{f.query_id}.sql`")
        lines.append("")
    lines += ["## Proposed remediation", "", "```sql", f.proposed_remediation, "```", ""]
    if a is not None:
        lines += [
            "## Assessment (reasoning tier)",
            "",
            f"**Material:** {'yes' if a.material else 'no'}  ",
            f"**Rationale:** {a.rationale}  ",
            f"**Blast radius:** {a.blast_radius}  ",
            "",
            "### Remediation plan",
            "",
            a.remediation_plan,
            "",
        ]
        if a.remediation_sql:
            lines += [
                "### Proposed statement(s) - for review, not executed",
                "",
                "```sql",
                a.remediation_sql,
                "```",
                "",
            ]
    lines += [
        "---",
        "Opened by an always-on sweep agent (read-only). No change was made. "
        f"Any mutation runs only through the approval-gated triage path. `{MARK}:{f.fingerprint}`",
    ]
    return "\n".join(lines)


def open_issue(f: Finding, a: Assessment | None, scenario: str, dry_run: bool = False) -> dict:
    existing = find_open_issue(f.fingerprint)
    if existing:
        return {"action": "exists", "url": existing["html_url"], "number": existing["number"]}
    title = f"[{scenario}] {f.severity.value}: {f.title}"
    payload = {
        "title": title[:250],
        "body": issue_body(f, a, scenario),
        "labels": [scenario, f"severity:{f.severity.value}", f"provider:{f.provider}", "oape-demo"],
    }
    if dry_run or os.environ.get("OAPE_DRY_RUN") == "1":
        return {"action": "dry-run", "title": title}
    r = httpx.post(f"{API}/repos/{_repo()}/issues", json=payload, headers=_headers(), timeout=30)
    if r.status_code == 422 and "label" in r.text.lower():
        payload.pop("labels")
        r = httpx.post(
            f"{API}/repos/{_repo()}/issues", json=payload, headers=_headers(), timeout=30
        )
    r.raise_for_status()
    j = r.json()
    return {"action": "created", "url": j["html_url"], "number": j["number"]}


def close_demo_issues(scenario: str | None = None) -> int:
    """Reset helper: close every open issue the sweeps opened (label oape-demo)."""
    n = 0
    for it in list_open_demo_issues(scenario):
        httpx.patch(
            f"{API}/repos/{_repo()}/issues/{it['number']}",
            json={"state": "closed", "state_reason": "not_planned"},
            headers=_headers(),
            timeout=30,
        ).raise_for_status()
        n += 1
    return n
