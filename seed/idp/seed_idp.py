"""IdP seed - Microsoft Entra ID (chosen; see WORK_ORDER.md).

Uses Microsoft Graph with the same app registration the entra_id StackQL provider uses
(AZURE_TENANT_ID / AZURE_CLIENT_ID / AZURE_CLIENT_SECRET, client credentials). Needs these Graph
application permissions with admin consent: User.ReadWrite.All, Group.ReadWrite.All,
GroupMember.ReadWrite.All. Without them every call returns 403 and the seed records the gap
instead of failing the run.

Plants:
  users  : one per GitHub org member (oape-demo-<login>@<tenant default domain>, department
           "oape-demo" as the tag) so the GitHub-members-to-IdP join mostly matches
  leaver : oape-demo-leaver, accountEnabled=false, still a member of the privileged group
  group  : IDP_PRIVILEGED_GROUP (security group) containing the leaver and one active user

Teardown deletes users whose department is the demo tag and the group by display name.
"""

from __future__ import annotations

import json
import secrets

import httpx

from oape_agents.common.config import env, settings
from seed.common import SeedReport, gh, log, name, required, save_state, tag_value

GRAPH = "https://graph.microsoft.com/v1.0"


def _token() -> str:
    r = httpx.post(
        f"https://login.microsoftonline.com/{env('AZURE_TENANT_ID', required=True)}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": env("AZURE_CLIENT_ID", required=True),
            "client_secret": env("AZURE_CLIENT_SECRET", required=True),
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


class Graph:
    def __init__(self) -> None:
        self.h = {"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"}

    def get(self, path: str, **params) -> dict:
        r = httpx.get(f"{GRAPH}{path}", headers=self.h, params=params, timeout=30)
        if r.status_code == 403:
            raise PermissionError(r.json().get("error", {}).get("message", "403"))
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict) -> dict:
        r = httpx.post(f"{GRAPH}{path}", headers=self.h, json=body, timeout=30)
        if r.status_code == 403:
            raise PermissionError(r.json().get("error", {}).get("message", "403"))
        r.raise_for_status()
        return r.json() if r.content else {}

    def delete(self, path: str) -> None:
        r = httpx.delete(f"{GRAPH}{path}", headers=self.h, timeout=30)
        if r.status_code not in (204, 404):
            r.raise_for_status()


PERMISSION_NOTE = (
    "entra_id: Graph refused (insufficient privileges). Grant the app registration the application "
    "permissions User.ReadWrite.All, Group.ReadWrite.All, GroupMember.ReadWrite.All with admin consent, "
    "then re-run `make seed`. Until then the entitlements IdP join runs from fallbacks/."
)


def _default_domain(g: Graph) -> str:
    org = g.get("/organization", **{"$select": "verifiedDomains"})["value"][0]
    for d in org["verifiedDomains"]:
        if d.get("isDefault"):
            return d["name"]
    return org["verifiedDomains"][0]["name"]


def _github_logins() -> list[str]:
    out = gh(["api", f"orgs/{settings().github_org}/members", "--jq", ".[].login"], check=False)
    return [x.strip() for x in out.splitlines() if x.strip()]


def _ensure_user(
    g: Graph, nick: str, display: str, domain: str, enabled: bool, rep: SeedReport
) -> str:
    upn = f"{nick}@{domain}"
    found = g.get("/users", **{"$filter": f"userPrincipalName eq '{upn}'", "$select": "id"})[
        "value"
    ]
    if found:
        rep.existed.append(upn)
        return found[0]["id"]
    u = g.post(
        "/users",
        {
            "accountEnabled": enabled,
            "displayName": display,
            "mailNickname": nick,
            "userPrincipalName": upn,
            "department": tag_value(),
            "passwordProfile": {
                "forceChangePasswordNextSignIn": True,
                "password": secrets.token_urlsafe(20),
            },
        },
    )
    rep.created.append(upn)
    log("entra", f"created user {upn} enabled={enabled}", "ok")
    return u["id"]


def seed(rep: SeedReport) -> None:
    if not required("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
        rep.substitutions.append("entra_id: Azure app credentials not set - IdP not seeded")
        return
    g = Graph()
    try:
        domain = _default_domain(g)
        group_name = settings().idp_privileged_group
        groups = g.get("/groups", **{"$filter": f"displayName eq '{group_name}'", "$select": "id"})[
            "value"
        ]
        if groups:
            gid = groups[0]["id"]
            rep.existed.append(group_name)
        else:
            gid = g.post(
                "/groups",
                {
                    "displayName": group_name,
                    "mailEnabled": False,
                    "mailNickname": group_name,
                    "securityEnabled": True,
                    "description": f"{tag_value()}: privileged cloud admins (planted)",
                },
            )["id"]
            rep.created.append(group_name)
        ids: dict[str, str] = {}
        logins = _github_logins()
        for login in logins:
            nick = name(login.lower())[:60]
            ids[login] = _ensure_user(g, nick, f"{login} (oape demo)", domain, True, rep)
        leaver = name("leaver")
        ids["leaver"] = _ensure_user(
            g, leaver, "Former Engineer (oape demo leaver)", domain, False, rep
        )
        members = {m["id"] for m in g.get(f"/groups/{gid}/members", **{"$select": "id"})["value"]}
        for key in ("leaver", *(logins[:1])):
            uid = ids[key]
            if uid not in members:
                g.post(
                    f"/groups/{gid}/members/$ref", {"@odata.id": f"{GRAPH}/directoryObjects/{uid}"}
                )
                rep.created.append(f"{group_name} member {key}")
        save_state("idp", {"group": group_name, "domain": domain, "users": ids})
    except PermissionError as e:
        rep.substitutions.append(PERMISSION_NOTE + f" ({e})")
        log("entra", PERMISSION_NOTE, "warn")


def teardown(rep: SeedReport) -> None:
    if not required("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
        return
    g = Graph()
    try:
        users = g.get(
            "/users",
            **{"$filter": f"department eq '{tag_value()}'", "$select": "id,userPrincipalName"},
        )["value"]
        for u in users:
            g.delete(f"/users/{u['id']}")
            rep.deleted.append(u["userPrincipalName"])
        group_name = settings().idp_privileged_group
        for grp in g.get(
            "/groups", **{"$filter": f"displayName eq '{group_name}'", "$select": "id"}
        )["value"]:
            g.delete(f"/groups/{grp['id']}")
            rep.deleted.append(group_name)
    except PermissionError as e:
        rep.substitutions.append(PERMISSION_NOTE + f" ({e})")


def status() -> None:
    if not required("AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET"):
        log("entra", "not configured")
        return
    g = Graph()
    try:
        users = g.get(
            "/users",
            **{
                "$filter": f"department eq '{tag_value()}'",
                "$select": "userPrincipalName,accountEnabled",
            },
        )["value"]
        log("entra", f"demo users: {json.dumps(users)}")
    except PermissionError as e:
        log("entra", f"403 - {e}", "warn")
