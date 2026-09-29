"""Prompt loader. Every model-facing instruction is prose in finops/prompts/<role>.md:

    sweep.md      the scheduled sweep: intent per provider, tenancy, output contract, guardrails
    reasoning.md  the per-provider judgement and statement drafting
    discovery.md  the shared briefing on how to discover resources and IO contracts through the
                  StackQL tools, appended to every model-facing prompt together with the
                  server's own instructions (MCP resource stackql://docs/instructions)

The code substitutes `{{ placeholders }}` from explicit overrides, then the matching upper-case
environment variable (demo_prefix -> DEMO_PREFIX), then the documented defaults in config.
A placeholder with no value is an error naming the variable. No SQL and no resource names
live here: the prompts state intent and scope; the models discover the rest at run time.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .config import PROMPTS_DIR, param_defaults

_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")
SERVER_INSTRUCTIONS_URI = "stackql://docs/instructions"


class PromptError(ValueError):
    pass


def placeholders(text: str) -> list[str]:
    return sorted(set(_PLACEHOLDER_RE.findall(text)))


def load_prompt(name: str, base: Path = PROMPTS_DIR) -> str:
    path = base / f"{name}.md"
    if not path.exists():
        raise PromptError(f"prompt {name!r} not found at {path}")
    return path.read_text(encoding="utf-8")


def render(text: str, name: str, overrides: dict[str, str]) -> str:
    """Substitute every placeholder in `text`; a missing value names the env variable."""
    values: dict[str, str] = {}
    missing: list[str] = []
    for key in placeholders(text):
        v = str(overrides.get(key, "") or "")
        if v == "":
            v = os.environ.get(key.upper(), "")
        if v == "":
            v = param_defaults().get(key, "")
        if v == "":
            missing.append(key)
        values[key] = v
    if missing:
        names = ", ".join(f"{{{{ {k} }}}} (env {k.upper()})" for k in missing)
        raise PromptError(f"prompt {name}: missing placeholder value(s) {names}")
    return _PLACEHOLDER_RE.sub(lambda m: values[m.group(1)], text).strip()


def render_prompt(name: str, base: Path = PROMPTS_DIR, **overrides: str) -> str:
    return render(load_prompt(name, base), name, {k: str(v) for k, v in overrides.items()})


async def read_server_instructions(server) -> str:
    """The server's own guidance, published as the MCP resource stackql://docs/instructions.
    Read through the Agents SDK server (MCP resources/read). Raises on any failure; the caller
    decides whether to continue without it."""
    res = await server.read_resource(SERVER_INSTRUCTIONS_URI)
    texts = [getattr(c, "text", "") for c in (getattr(res, "contents", None) or [])]
    text = "\n".join(t for t in texts if t).strip()
    if not text:
        raise PromptError(f"{SERVER_INSTRUCTIONS_URI} returned no text")
    return text


async def discovery_briefing(server, base: Path = PROMPTS_DIR, *, notes: list[str]) -> str:
    """discovery.md plus the server's current instructions. If the resource read fails the
    briefing is still returned and the failure is recorded in `notes` for the console."""
    briefing = render_prompt("discovery", base)
    try:
        instructions = await read_server_instructions(server)
    except Exception as e:  # noqa: BLE001 - any failure here is non-fatal by design
        notes.append(
            f"{SERVER_INSTRUCTIONS_URI} not read ({type(e).__name__}: {e}); continuing without it"
        )
        return briefing
    return f"{briefing}\n\n## StackQL server instructions ({SERVER_INSTRUCTIONS_URI})\n\n{instructions}"
