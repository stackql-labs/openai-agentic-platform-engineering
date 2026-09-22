"""Query library loader. All SQL lives in queries/ - one file per query with a header comment:

    -- id: cspm/aws_s3_bucket_public
    -- scenario: cspm
    -- providers: aws
    -- params: aws_region
    -- expected_columns: bucket, region, exposure, evidence
    -- description: one line

Parameters are `{{ name }}` placeholders substituted from settings().tenancy (plus overrides).
Agent code loads queries by id; there are no inline SQL strings in agent code."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import QUERIES_DIR, settings

_HEADER_RE = re.compile(r"^--\s*([a-z_]+)\s*:\s*(.*)$")
_PARAM_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


@dataclass
class Query:
    id: str
    path: Path
    scenario: str
    providers: list[str]
    params: list[str]
    expected_columns: list[str]
    description: str
    sql: str
    meta: dict[str, str] = field(default_factory=dict)

    def render(self, **overrides: str) -> str:
        values = {**settings().tenancy, **{k: str(v) for k, v in overrides.items()}}
        missing = [p for p in self.params if values.get(p, "") == ""]
        if missing:
            raise ValueError(f"query {self.id}: missing params {missing}")

        def sub(m: re.Match) -> str:
            key = m.group(1)
            if key not in values:
                raise ValueError(f"query {self.id}: unknown placeholder {{{{ {key} }}}}")
            return values[key]

        return _PARAM_RE.sub(sub, self.sql).strip()


def _split(meta: dict[str, str], key: str) -> list[str]:
    return [x.strip() for x in meta.get(key, "").split(",") if x.strip()]


def _parse(path: Path) -> Query:
    text = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body: list[str] = []
    in_header = True
    for line in text.splitlines():
        if in_header:
            m = _HEADER_RE.match(line.strip())
            if m:
                meta[m.group(1)] = m.group(2).strip()
                continue
            if line.strip() == "" or line.strip().startswith("--"):
                continue
            in_header = False
        body.append(line)
    sql = "\n".join(body).strip().rstrip(";")
    rel = path.relative_to(QUERIES_DIR).as_posix()[: -len(path.suffix)]
    params_declared = _split(meta, "params")
    params_used = sorted(set(_PARAM_RE.findall(sql)))
    return Query(
        id=meta.get("id", rel),
        path=path,
        scenario=meta.get("scenario", rel.split("/")[0]),
        providers=_split(meta, "providers"),
        params=params_declared or params_used,
        expected_columns=_split(meta, "expected_columns"),
        description=meta.get("description", ""),
        sql=sql,
        meta=meta,
    )


def list_queries(scenario: str | None = None) -> list[Query]:
    out = []
    for p in sorted(QUERIES_DIR.rglob("*.sql")):
        q = _parse(p)
        if scenario is None or q.scenario == scenario:
            out.append(q)
    return out


def load_query(query_id: str) -> Query:
    for q in list_queries():
        if q.id == query_id:
            return q
    raise FileNotFoundError(f"no query with id {query_id!r} under {QUERIES_DIR}")


def render_query(query_id: str, **overrides: str) -> str:
    return load_query(query_id).render(**overrides)


def sql_list(values: list[str]) -> str:
    """Render a literal IN (...) list: fan-out parameters must be literals (no subqueries)."""
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def render_union(query_id: str, param: str, values: list[str], **overrides: str) -> str:
    """Render one SELECT per value and UNION ALL them. Used where a provider method takes one
    key per call and the response does not echo it (attached policies per user, service accounts
    per project): each SELECT projects its key as a literal column so rows stay attributed."""
    q = load_query(query_id)
    if not values:
        raise ValueError(f"render_union({query_id}): no values for {param}")
    parts = [q.render(**{param: v}, **overrides) for v in values]
    separator = "\nUNION ALL\n"
    return separator.join(parts)


# --- local execution (seed checks, snapshots, tests) - not the agent path ------------------


class StackQLError(RuntimeError):
    pass


def stackql_exec(
    sql: str,
    *,
    backend_dsn: str | None = None,
    timeout: int = 600,
    extra_args: list[str] | None = None,
) -> list[dict]:
    """Run one statement with the stackql CLI and return rows as dicts (empty for DDL/DML)."""
    s = settings()
    cmd = [s.stackql_bin, "exec", "--approot", s.stackql_approot.as_posix(), "--output", "json"]
    if backend_dsn:
        cmd += ["--sqlBackend", json.dumps({"dbEngine": "sqlite3_embedded", "dsn": backend_dsn})]
    if extra_args:
        cmd += extra_args
    cmd.append(sql)
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, env=dict(os.environ)
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        raise StackQLError(err or out or f"stackql exited {proc.returncode}")
    if (not out or out == "null") and err and "error" in err.lower():
        # parser and provider errors can arrive on stderr with a zero exit and no rows
        raise StackQLError(err)
    if not out or out == "null":
        return []
    if out.startswith("[") or out.startswith("{"):
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            return [{"message": out}]
        if isinstance(data, dict):
            return [data]
        return data
    # stackql prints a message for DDL/DML and for provider-side errors on a zero exit
    if "error" in out.lower() and "completed" not in out.lower():
        raise StackQLError(out)
    return [{"message": out}]


def stackql_validate(sql: str) -> tuple[bool, str]:
    """Plan a SELECT without executing (stackql exec --dryrun)."""
    try:
        stackql_exec(sql, extra_args=["--dryrun"], timeout=120)
        return True, ""
    except StackQLError as e:
        return False, str(e)
