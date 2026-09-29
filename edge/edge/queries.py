"""Query loader. One .sql file per query under edge/queries/ with a header comment:

    -- id: edge/zone_traffic
    -- providers: cloudflare
    -- params: cloudflare_zone_id, since, until
    -- expected_columns: datetime, client_country_name, ...
    -- description: one line

`{{ name }}` placeholders are substituted from the environment (lower-case param -> the matching
upper-case variable, e.g. cloudflare_zone_id -> CLOUDFLARE_ZONE_ID) plus explicit overrides passed
by code. A missing value fails fast naming the variable. Agent code loads queries by id; the only
statements in code are rendered from these files."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import QUERIES_DIR

_HEADER_RE = re.compile(r"^--\s*([a-z_]+)\s*:\s*(.*)$")
_PARAM_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


@dataclass
class Query:
    id: str
    path: Path
    providers: list[str]
    params: list[str]
    expected_columns: list[str]
    description: str
    sql: str
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def is_select(self) -> bool:
        return self.sql.lstrip().upper().startswith(("SELECT", "WITH"))

    def render(self, **overrides: object) -> str:
        values = {k: str(v) for k, v in overrides.items()}
        missing: list[str] = []
        for p in self.params:
            if values.get(p, "") != "":
                continue
            from_env = os.environ.get(p.upper(), "")
            if from_env == "":
                missing.append(f"{p} (set {p.upper()})")
            else:
                values[p] = from_env
        if missing:
            raise ValueError(f"query {self.id}: missing parameters: {', '.join(missing)}")

        def sub(m: re.Match[str]) -> str:
            key = m.group(1)
            if key not in values:
                raise ValueError(f"query {self.id}: unknown placeholder {{{{ {key} }}}}")
            return values[key]

        return _PARAM_RE.sub(sub, self.sql).strip()


def _split(meta: dict[str, str], key: str) -> list[str]:
    return [x.strip() for x in meta.get(key, "").split(",") if x.strip()]


def _parse(path: Path) -> Query:
    meta: dict[str, str] = {}
    body: list[str] = []
    in_header = True
    for line in path.read_text(encoding="utf-8").splitlines():
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
    declared = _split(meta, "params")
    used = sorted(set(_PARAM_RE.findall(sql)))
    undeclared = [p for p in used if p not in declared]
    if declared and undeclared:
        raise ValueError(f"query {path}: placeholders not declared in params: {undeclared}")
    return Query(
        id=meta.get("id", f"edge/{rel}"),
        path=path,
        providers=_split(meta, "providers"),
        params=declared or used,
        expected_columns=_split(meta, "expected_columns"),
        description=meta.get("description", ""),
        sql=sql,
        meta=meta,
    )


def list_queries() -> list[Query]:
    return [_parse(p) for p in sorted(QUERIES_DIR.rglob("*.sql"))]


def load_query(query_id: str) -> Query:
    for q in list_queries():
        if q.id == query_id:
            return q
    raise FileNotFoundError(f"no query with id {query_id!r} under {QUERIES_DIR}")


def render_query(query_id: str, **overrides: object) -> str:
    return load_query(query_id).render(**overrides)


def sql_literal(value: str) -> str:
    """Escape a value for use inside a single-quoted SQL string literal."""
    return value.replace("'", "''")
