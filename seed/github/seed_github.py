"""GitHub seed (gh CLI as the org admin; the collaborator's own token to accept the invite).

Plants in GITHUB_ORG:
  <prefix>-checkout  : private repo, topic oape-demo, default branch unprotected, a deploy key
                       titled "<prefix>-legacy-deploy-key" (private half discarded), an outside
                       collaborator (GITHUB_DEMO_COLLABORATOR) with admin permission, and a
                       production deployment record (for the triage "recent deployments" query)
  <prefix>-findings  : the repo the sweeps file issues into (GITHUB_ISSUES_REPO)

"Stale" for the deploy key is measured from its real creation date against
DEPLOY_KEY_MAX_AGE_DAYS (0 for the demo). The runbook narrates the real age.

Teardown removes the key, the collaborator and the repos. Deleting a repo needs the delete_repo
scope on the gh token; when that is missing the repo is archived and renamed instead and the
command to grant the scope is printed (recorded in WORK_ORDER.md).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import httpx

from oape_agents.common.config import env, settings
from seed.common import SeedReport, gh, log, name, save_state

API = "https://api.github.com"


def _org() -> str:
    return settings().github_org


def _repos() -> dict[str, str]:
    return {"checkout": f"{_org()}/{name('checkout')}", "findings": f"{_org()}/{name('findings')}"}


def _repo_exists(full: str) -> bool:
    """Exact-name check: GitHub redirects a renamed repo's old name, so an archived-and-renamed
    demo repo must not count as present."""
    out = gh(["api", f"repos/{full}", "--jq", ".full_name"], check=False).strip()
    return out.lower() == full.lower()


def _ensure_repo(full: str, desc: str, rep: SeedReport, private: bool = True) -> None:
    if _repo_exists(full):
        rep.existed.append(full)
        return
    gh(
        [
            "repo",
            "create",
            full,
            "--private" if private else "--public",
            "--description",
            desc,
            "--add-readme",
        ]
    )
    gh(["repo", "edit", full, "--add-topic", "oape-demo"])
    rep.created.append(full)
    log("github", f"created {full}", "ok")


def _deploy_key(full: str, rep: SeedReport) -> None:
    title = name("legacy-deploy-key")
    keys = json.loads(gh(["api", f"repos/{full}/keys"]) or "[]")
    if any(k["title"] == title for k in keys):
        rep.existed.append(title)
        return
    with tempfile.TemporaryDirectory() as d:
        kp = Path(d) / "key"
        subprocess.run(
            ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", title, "-f", str(kp)], check=True
        )
        pub = (Path(d) / "key.pub").read_text().strip()
        kp.unlink()  # the private half is discarded: the key exists, nobody holds it
    gh(
        ["api", f"repos/{full}/keys", "--input", "-"],
        input_json={"title": title, "key": pub, "read_only": False},
    )
    rep.created.append(title)
    log("github", f"added deploy key {title} to {full} (private key discarded)", "ok")


def _collaborator(full: str, rep: SeedReport) -> None:
    login = env("GITHUB_DEMO_COLLABORATOR")
    if not login:
        rep.substitutions.append(
            "github: GITHUB_DEMO_COLLABORATOR not set - outside collaborator not seeded"
        )
        return
    perm = gh(
        ["api", f"repos/{full}/collaborators/{login}/permission", "--jq", ".permission"],
        check=False,
    ).strip()
    if perm == "admin":
        rep.existed.append(f"collaborator {login}")
        return
    gh(["api", "-X", "PUT", f"repos/{full}/collaborators/{login}", "-f", "permission=admin"])
    token = env("GITHUB_COLLABORATOR_TOKEN")
    if not token:
        rep.substitutions.append(
            f"github: invited {login} as admin; no GITHUB_COLLABORATOR_TOKEN to accept, invitation is pending"
        )
        return
    h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    for _ in range(6):
        inv = httpx.get(f"{API}/user/repository_invitations", headers=h, timeout=30).json()
        match = [i for i in inv if i["repository"]["full_name"].lower() == full.lower()]
        if match:
            httpx.patch(
                f"{API}/user/repository_invitations/{match[0]['id']}", headers=h, timeout=30
            ).raise_for_status()
            rep.created.append(f"collaborator {login} (admin)")
            log("github", f"{login} accepted admin invitation on {full}", "ok")
            return
        time.sleep(3)
    rep.substitutions.append(f"github: invitation for {login} not visible to accept; pending")


def _deployment(full: str, rep: SeedReport) -> None:
    deps = json.loads(gh(["api", f"repos/{full}/deployments?environment=production"]) or "[]")
    if deps:
        rep.existed.append("deployment")
        return
    d = json.loads(
        gh(
            ["api", f"repos/{full}/deployments", "--input", "-"],
            input_json={
                "ref": "main",
                "environment": "production",
                "auto_merge": False,
                "required_contexts": [],
                "description": "checkout 1.4.2 - connection pool defaults changed",
            },
        )
    )
    gh(
        ["api", f"repos/{full}/deployments/{d['id']}/statuses", "--input", "-"],
        input_json={"state": "success", "description": "deployed"},
    )
    rep.created.append("deployment production")


def seed(rep: SeedReport) -> None:
    r = _repos()
    _ensure_repo(r["checkout"], "oape demo: checkout service (planted governance findings)", rep)
    _deploy_key(r["checkout"], rep)
    _collaborator(r["checkout"], rep)
    _deployment(r["checkout"], rep)
    _ensure_repo(r["findings"], "oape demo: findings filed by the sweep agents", rep)
    if settings().github_issues_repo.lower() != r["findings"].lower():
        rep.substitutions.append(
            f"GITHUB_ISSUES_REPO={settings().github_issues_repo} differs from seeded {r['findings']}"
        )
    save_state("github", {"repos": r})


def _delete_repo(full: str, rep: SeedReport) -> None:
    if not _repo_exists(full):
        return
    topics = json.loads(gh(["api", f"repos/{full}/topics", "--jq", ".names"]) or "[]")
    if "oape-demo" not in topics:
        log("github", f"{full} lacks the oape-demo topic: leaving it", "warn")
        return
    out = subprocess.run(
        ["gh", "repo", "delete", full, "--yes"],
        capture_output=True,
        text=True,
        env={k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "GH_TOKEN")},
    )
    if out.returncode == 0:
        rep.deleted.append(full)
        return
    ts = time.strftime("%Y%m%d%H%M%S")
    new = f"{full.split('/')[1]}-archived-{ts}"
    gh(
        [
            "repo",
            "edit",
            full,
            "--description",
            "oape demo: archived by teardown (delete_repo scope missing)",
        ],
        check=False,
    )
    gh(
        ["api", "-X", "PATCH", f"repos/{full}", "-f", f"name={new}", "-F", "archived=true"],
        check=False,
    )
    rep.substitutions.append(
        f"github: could not delete {full} (token lacks delete_repo); archived as {new}. "
        "Grant once with: gh auth refresh -h github.com -s delete_repo"
    )


def teardown(rep: SeedReport) -> None:
    r = _repos()
    full = r["checkout"]
    if _repo_exists(full):
        for k in json.loads(gh(["api", f"repos/{full}/keys"]) or "[]"):
            if k["title"].startswith(settings().demo_prefix):
                gh(["api", "-X", "DELETE", f"repos/{full}/keys/{k['id']}"])
                rep.deleted.append(f"deploy key {k['title']}")
        login = env("GITHUB_DEMO_COLLABORATOR")
        if login:
            gh(["api", "-X", "DELETE", f"repos/{full}/collaborators/{login}"], check=False)
            for inv in json.loads(gh(["api", f"repos/{full}/invitations"]) or "[]"):
                if inv["invitee"]["login"].lower() == login.lower():
                    gh(
                        ["api", "-X", "DELETE", f"repos/{full}/invitations/{inv['id']}"],
                        check=False,
                    )
            rep.deleted.append(f"collaborator {login}")
    _delete_repo(r["checkout"], rep)
    _delete_repo(r["findings"], rep)


def status() -> None:
    for k, full in _repos().items():
        exists = _repo_exists(full)
        log("github", f"{k}: {full} {'present' if exists else 'absent'}")
        if exists and k == "checkout":
            keys = json.loads(gh(["api", f"repos/{full}/keys"]) or "[]")
            log("github", f"  deploy keys: {[(x['title'], x['created_at']) for x in keys]}")
            collab = json.loads(
                gh(["api", f"repos/{full}/collaborators?affiliation=outside"]) or "[]"
            )
            log(
                "github",
                f"  outside collaborators: {[(c['login'], c.get('role_name')) for c in collab]}",
            )
            prot = gh(
                ["api", f"repos/{full}/branches/main", "--jq", ".protected"], check=False
            ).strip()
            log("github", f"  main protected: {prot}")
