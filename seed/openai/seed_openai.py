"""OpenAI org seed (Administration API, needs OPENAI_ADMIN_KEY - an sk-admin key).

Plants two throwaway projects (<prefix>-checkout, <prefix>-batch) and a service account with an
API key in the first one. The key value returned at creation is discarded immediately. "Old" is
measured from the real created_at against OPENAI_KEY_MAX_AGE_DAYS (0 for the demo).

Projects cannot be deleted through the API; teardown archives them and deletes the service
account. Archived demo projects remain visible in the org, filtered by the name prefix.
"""

from __future__ import annotations

import httpx

from oape_agents.common.config import env
from seed.common import SeedReport, log, name, required, save_state

API = "https://api.openai.com/v1/organization"
KEY_NOTE = (
    "openai_admin: OPENAI_ADMIN_KEY not set - create an Admin API key under Organization settings -> "
    "Admin keys, add it to .env, re-run `make seed`. Until then the closer runs from fallbacks/."
)


def _h() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {env('OPENAI_ADMIN_KEY', required=True)}",
        "Content-Type": "application/json",
    }


def _projects() -> list[dict]:
    out, after = [], None
    while True:
        r = httpx.get(
            f"{API}/projects",
            headers=_h(),
            params={
                "limit": 100,
                "include_archived": "true",
                **({"after": after} if after else {}),
            },
            timeout=30,
        )
        r.raise_for_status()
        j = r.json()
        out += j.get("data", [])
        if not j.get("has_more"):
            return out
        after = j.get("last_id")


def seed(rep: SeedReport) -> None:
    if not required("OPENAI_ADMIN_KEY"):
        rep.substitutions.append(KEY_NOTE)
        log("openai", KEY_NOTE, "warn")
        return
    existing = {p["name"]: p for p in _projects() if p.get("status") == "active"}
    ids = {}
    for suffix in ("checkout", "batch"):
        pn = name(suffix)
        if pn in existing:
            ids[pn] = existing[pn]["id"]
            rep.existed.append(pn)
            continue
        r = httpx.post(f"{API}/projects", headers=_h(), json={"name": pn}, timeout=30)
        r.raise_for_status()
        ids[pn] = r.json()["id"]
        rep.created.append(pn)
        log("openai", f"created project {pn}", "ok")
    pid = ids[name("checkout")]
    sa_name = name("ci-service-account")
    r = httpx.get(f"{API}/projects/{pid}/service_accounts", headers=_h(), timeout=30)
    r.raise_for_status()
    if any(sa["name"] == sa_name for sa in r.json().get("data", [])):
        rep.existed.append(sa_name)
    else:
        r = httpx.post(
            f"{API}/projects/{pid}/service_accounts",
            headers=_h(),
            json={"name": sa_name},
            timeout=30,
        )
        r.raise_for_status()
        j = r.json()
        j.pop("api_key", None)  # discarded on purpose: the key exists, nobody holds it
        rep.created.append(sa_name)
        log(
            "openai",
            f"created service account {sa_name} in {name('checkout')} (key discarded)",
            "ok",
        )
    save_state("openai", {"projects": ids})


def teardown(rep: SeedReport) -> None:
    if not required("OPENAI_ADMIN_KEY"):
        return
    for p in _projects():
        if not p["name"].startswith(name("")) or p.get("status") != "active":
            continue
        r = httpx.get(f"{API}/projects/{p['id']}/service_accounts", headers=_h(), timeout=30)
        for sa in r.json().get("data", []):
            httpx.delete(
                f"{API}/projects/{p['id']}/service_accounts/{sa['id']}", headers=_h(), timeout=30
            )
            rep.deleted.append(f"service account {sa['name']}")
        httpx.post(f"{API}/projects/{p['id']}/archive", headers=_h(), timeout=30).raise_for_status()
        rep.deleted.append(f"project {p['name']} (archived)")


def status() -> None:
    if not required("OPENAI_ADMIN_KEY"):
        log("openai", "OPENAI_ADMIN_KEY not set")
        return
    for p in _projects():
        if p["name"].startswith(name("")):
            log("openai", f"project {p['name']} {p.get('status')}")
