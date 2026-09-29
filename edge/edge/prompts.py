"""Prompt loader. One markdown file per agent role under edge/prompts/:

    recon.md      the recon agent's intent (traffic in the window, the current rate limit rule)
    decide.md     the decision agent's intent (apply the policy, propose the exact statement)
    discovery.md  the shared briefing on discovering resources and contracts through the StackQL
                  tools; appended to every model-facing prompt, followed by the server's own
                  instructions (the MCP resource stackql://docs/instructions) when they could be read

`{{ name }}` placeholders are substituted from explicit values passed by code plus the environment
(lower-case name -> the matching upper-case variable, e.g. cloudflare_zone_id -> CLOUDFLARE_ZONE_ID).
A missing value fails fast naming the variable. The prompts carry no resource names, columns or SQL:
the agents discover those at run time."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .config import PROMPTS_DIR

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
DISCOVERY = "discovery"
ROLES = ("recon", "decide")

SERVER_INSTRUCTIONS_URI = "stackql://docs/instructions"


def prompt_path(name: str) -> Path:
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"no prompt {name!r}: expected {path}")
    return path


def placeholders(name: str) -> list[str]:
    """The distinct placeholder names a prompt file declares, in order of first use."""
    seen: list[str] = []
    for key in _PLACEHOLDER_RE.findall(prompt_path(name).read_text(encoding="utf-8")):
        if key not in seen:
            seen.append(key)
    return seen


def render_prompt(name: str, **values: object) -> str:
    """Read edge/prompts/<name>.md and substitute every placeholder, env as the fallback."""
    text = prompt_path(name).read_text(encoding="utf-8")
    resolved = {k: str(v) for k, v in values.items() if str(v) != ""}
    missing: list[str] = []
    for key in placeholders(name):
        if key in resolved:
            continue
        from_env = os.environ.get(key.upper(), "")
        if from_env == "":
            missing.append(f"{key} (set {key.upper()})")
        else:
            resolved[key] = from_env
    if missing:
        raise ValueError(f"prompt {name}: missing values: {', '.join(missing)}")
    return _PLACEHOLDER_RE.sub(lambda m: resolved[m.group(1)], text).strip()


def discovery_briefing(server_instructions: str | None = None) -> str:
    """The shared briefing, with the server's published instructions appended when available."""
    text = render_prompt(DISCOVERY)
    if server_instructions and server_instructions.strip():
        text += (
            f"\n\n# Server instructions ({SERVER_INSTRUCTIONS_URI}, read at startup)\n\n"
            + server_instructions.strip()
        )
    return text


def instructions_for(role: str, briefing: str, **values: object) -> str:
    """Full instructions for one agent role: its intent prompt followed by the discovery briefing."""
    if role not in ROLES:
        raise ValueError(f"unknown prompt role {role!r} (one of {ROLES})")
    return render_prompt(role, **values) + "\n\n" + briefing
