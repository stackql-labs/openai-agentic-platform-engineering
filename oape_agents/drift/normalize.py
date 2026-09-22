"""Normalise raw provider rows (as materialized by StackQL) and Terraform state into one comparable
attribute shape per resource type, so a delta or a tfstate comparison is a string compare of
attrs_json. Every normaliser returns a dict with sorted, stable content."""

from __future__ import annotations

import json
from typing import Any


def _j(v: Any) -> Any:
    """Parse JSON-in-a-string columns; pass through everything else."""
    if isinstance(v, str):
        t = v.strip()
        if t in ("", "null"):
            return None
        if t[0] in "[{":
            try:
                return json.loads(t)
            except json.JSONDecodeError:
                return v
    return v


def _items(v: Any) -> list:
    """AWS native shape: {"item": {...}} or {"item": [...]} -> list."""
    v = _j(v)
    if isinstance(v, dict) and "item" in v:
        inner = v["item"]
        return inner if isinstance(inner, list) else [inner]
    if isinstance(v, list):
        return v
    return []


def aws_tags(v: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for t in _items(v):
        if isinstance(t, dict):
            k = t.get("key", t.get("Key"))
            val = t.get("value", t.get("Value"))
            if k is not None and not str(k).startswith("aws:"):
                out[str(k)] = str(val)
    return dict(sorted(out.items()))


def aws_ingress(ip_permissions: Any) -> list[str]:
    rules: set[str] = set()
    for perm in _items(ip_permissions):
        if not isinstance(perm, dict):
            continue
        proto = str(perm.get("ipProtocol", "-1"))
        f, t = str(perm.get("fromPort", "")), str(perm.get("toPort", ""))
        cidrs = [str(r.get("cidrIp")) for r in _items(perm.get("ipRanges")) if isinstance(r, dict)]
        groups = [str(g.get("groupId")) for g in _items(perm.get("groups")) if isinstance(g, dict)]
        for src in cidrs + groups:
            rules.add(f"{proto}:{f}-{t}:{src}")
    return sorted(rules)


def ec2_instance(row: dict) -> dict:
    return {
        "instance_type": row.get("instance_type"),
        "state": row.get("state"),
        "tags": aws_tags(row.get("tags")),
    }


def security_group(row: dict) -> dict:
    return {
        "name": row.get("group_name"),
        "ingress": aws_ingress(row.get("ip_permissions")),
        "tags": aws_tags(row.get("tags")),
    }


def s3_bucket(row: dict) -> dict:
    return {"exists": True}


def azure_nsg(row: dict) -> dict:
    rules = []
    for r in _j(row.get("security_rules")) or []:
        p = r.get("properties", {})
        rules.append(
            f"{r.get('name')}:{p.get('direction')}:{p.get('access')}:{p.get('protocol')}:"
            f"{p.get('destinationPortRange')}:{p.get('sourceAddressPrefix')}:{p.get('priority')}"
        )
    tags = _j(row.get("tags")) or {}
    return {"name": row.get("name"), "rules": sorted(rules), "tags": dict(sorted(tags.items()))}


def google_firewall(row: dict) -> dict:
    allowed = []
    for a in _j(row.get("allowed")) or []:
        allowed.append(f"{a.get('IPProtocol')}:{','.join(a.get('ports', []))}")
    return {
        "name": row.get("name"),
        "direction": row.get("direction"),
        "disabled": str(row.get("disabled")).lower(),
        "source_ranges": sorted(_j(row.get("sourceRanges")) or []),
        "allowed": sorted(allowed),
        "priority": str(row.get("priority")),
    }


# --- terraform state --------------------------------------------------------------------


def tf_instance(attrs: dict) -> dict:
    tags = {k: str(v) for k, v in (attrs.get("tags_all") or attrs.get("tags") or {}).items()}
    return {
        "instance_type": attrs.get("instance_type"),
        "state": "running",
        "tags": dict(sorted(tags.items())),
    }


def tf_security_group(attrs: dict) -> dict:
    rules: set[str] = set()
    for ing in attrs.get("ingress") or []:
        proto = str(ing.get("protocol"))
        f, t = str(ing.get("from_port")), str(ing.get("to_port"))
        for cidr in ing.get("cidr_blocks") or []:
            rules.add(f"{proto}:{f}-{t}:{cidr}")
        for sg in ing.get("security_groups") or []:
            rules.add(f"{proto}:{f}-{t}:{sg}")
    tags = {k: str(v) for k, v in (attrs.get("tags_all") or attrs.get("tags") or {}).items()}
    return {"name": attrs.get("name"), "ingress": sorted(rules), "tags": dict(sorted(tags.items()))}


def tf_bucket(attrs: dict) -> dict:
    return {"exists": True}


def stable(d: dict) -> str:
    return json.dumps(d, sort_keys=True, separators=(",", ":"), default=str)
