"""Query library loader. All SQL lives in finops/queries/ - one file per query with a header:

    -- id: finops/aws_unattached_volumes
    -- providers: aws
    -- params: aws_region
    -- expected_columns: volume_id, size_gb, ...
    -- description: one line
    <SQL with {{ param }} placeholders>

Mutation templates under finops/queries/remediation/ carry `-- kind: mutation`. They are never
executed by this program: the models copy them into proposed_remediation for human review.

Placeholders are substituted from environment variables (lower-cased param -> the matching
upper-case env var, e.g. aws_region -> AWS_REGION) plus explicit overrides passed by code.
Missing values fail fast naming the variable. Agent code loads queries by id; there are no
inline SQL strings in agent code.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import QUERIES_DIR, param_defaults

_HEADER_RE = re.compile(r"^--\s*([a-z_]+)\s*:\s*(.*)$")
_PARAM_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")


class QueryError(ValueError):
    pass


@dataclass
class Query:
    id: str
    path: Path
    kind: str  # select | mutation
    providers: list[str]
    params: list[str]
    expected_columns: list[str]
    description: str
    sql: str
    meta: dict[str, str] = field(default_factory=dict)

    def values(self, overrides: dict[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for p in self.params:
            v = overrides.get(p, "")
            if v == "":
                v = os.environ.get(p.upper(), "")
            if v == "":
                v = param_defaults().get(p, "")
            out[p] = str(v)
        return out

    def render(self, **overrides: str) -> str:
        """Substitute every placeholder; a missing value is an error naming the env variable."""
        values = self.values({k: str(v) for k, v in overrides.items()})
        missing = [p for p in self.params if values.get(p, "") == ""]
        if missing:
            names = ", ".join(f"{p} (env {p.upper()})" for p in missing)
            raise QueryError(f"query {self.id}: missing parameter(s) {names}")
        return self._substitute(values, strict=True)

    def render_partial(self, **overrides: str) -> str:
        """Substitute what is available and leave the rest as <name> markers. Used for the
        mutation templates handed to the models, which fill resource identifiers themselves."""
        values = {k: v for k, v in self.values(overrides).items() if v != ""}
        return self._substitute(values, strict=False)

    def _substitute(self, values: dict[str, str], *, strict: bool) -> str:
        def sub(m: re.Match) -> str:
            key = m.group(1)
            if key in values:
                return values[key]
            if strict:
                raise QueryError(f"query {self.id}: unknown placeholder {{{{ {key} }}}}")
            return f"<{key}>"

        return _PARAM_RE.sub(sub, self.sql).strip()


def _split(meta: dict[str, str], key: str) -> list[str]:
    return [x.strip() for x in meta.get(key, "").split(",") if x.strip()]


def parse_query(path: Path, base: Path = QUERIES_DIR) -> Query:
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
    rel = path.relative_to(base).as_posix()[: -len(path.suffix)]
    declared = _split(meta, "params")
    used = sorted(set(_PARAM_RE.findall(sql)))
    if declared and set(declared) != set(used):
        raise QueryError(
            f"{path}: declared params {sorted(declared)} do not match placeholders {used}"
        )
    return Query(
        id=meta.get("id", f"finops/{rel}"),
        path=path,
        kind=meta.get("kind", "select"),
        providers=_split(meta, "providers"),
        params=declared or used,
        expected_columns=_split(meta, "expected_columns"),
        description=meta.get("description", ""),
        sql=sql,
        meta=meta,
    )


def list_queries(kind: str | None = None, base: Path = QUERIES_DIR) -> list[Query]:
    out = []
    for p in sorted(base.rglob("*.sql")):
        q = parse_query(p, base)
        if kind is None or q.kind == kind:
            out.append(q)
    return out


def load_query(query_id: str, base: Path = QUERIES_DIR) -> Query:
    for q in list_queries(base=base):
        if q.id == query_id:
            return q
    raise FileNotFoundError(f"no query with id {query_id!r} under {base}")


def render_query(query_id: str, **overrides: str) -> str:
    return load_query(query_id).render(**overrides)
