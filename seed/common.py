"""Shared helpers for seed scripts: settings, naming, tagging, logging, subprocess wrappers."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from oape_agents.common.config import REPO_ROOT, env, settings

for _stream in (sys.stdout, sys.stderr):
    try:  # Windows consoles default to cp1252; child tools print UTF-8 (box drawing, emoji)
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
console = Console(width=None if sys.stdout.isatty() else 140)

STATE_DIR = REPO_ROOT / "seed" / "state"


def tag_key() -> str:
    return settings().demo_tag_key


def tag_value() -> str:
    return settings().demo_tag_value


def prefix() -> str:
    return settings().demo_prefix


def name(suffix: str) -> str:
    return f"{prefix()}-{suffix}"


def log(step: str, msg: str, level: str = "info") -> None:
    color = {"info": "white", "ok": "green", "skip": "cyan", "warn": "yellow", "err": "red"}[level]
    safe = msg.encode("ascii", "replace").decode("ascii") if not sys.stdout.isatty() else msg
    console.print(f"[{step}] {safe}", style=color, markup=False, highlight=False)


@dataclass
class SeedReport:
    provider: str
    created: list[str] = field(default_factory=list)
    existed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    substitutions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.provider}: created={len(self.created)} existed={len(self.existed)} "
            f"deleted={len(self.deleted)} skipped={len(self.skipped)} errors={len(self.errors)}"
        )


def run(
    cmd: list[str],
    *,
    check: bool = True,
    input_text: str | None = None,
    env_extra: dict | None = None,
) -> str:
    """Run a command, return stdout. Raises with stderr on failure when check=True."""
    e = dict(os.environ)
    if env_extra:
        e.update(env_extra)
    proc = subprocess.run(cmd, capture_output=True, text=True, input=input_text, env=e)
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"{shlex.join(cmd)[:200]} -> {proc.returncode}: {proc.stderr.strip()[:800]}"
        )
    return proc.stdout


def gh(args: list[str], *, check: bool = True, input_json: dict | None = None) -> str:
    """gh CLI with GITHUB_TOKEN unset so the keyring identity (org admin) is used."""
    e = dict(os.environ)
    e.pop("GITHUB_TOKEN", None)
    e.pop("GH_TOKEN", None)
    cmd = ["gh", *args]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        input=json.dumps(input_json) if input_json is not None else None,
        env=e,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)[:160]} -> {proc.stderr.strip()[:600]}")
    return proc.stdout


def wait_for(desc: str, fn: Callable[[], bool], timeout: int = 600, interval: int = 10) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        if fn():
            return True
        time.sleep(interval)
    log(desc, f"timed out after {timeout}s", "warn")
    return False


def save_state(provider: str, data: dict) -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = STATE_DIR / f"{provider}.json"
    p.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    return p


def load_state(provider: str) -> dict:
    p = STATE_DIR / f"{provider}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def required(*names: str) -> bool:
    return all(env(n) != "" for n in names)
