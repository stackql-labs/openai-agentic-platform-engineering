"""Decision step (frontier tier, read-only server). Given the recon report and the threshold policy
from .env (edge/prompts/decide.md) it returns a structured decision. On tighten it also returns the
exact statement it proposes, having discovered the write contract itself (query library, then
list_methods / describe_method on the resource the recon read), plus a verification SELECT and a
rollback statement. The model supplies the judgement and the statements; code enforces the policy
envelope (only the two configured thresholds can ever be proposed) and the gate checks every
statement against its allowlist before an operator sees it. The model never executes anything."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal

from agents import Runner
from pydantic import BaseModel, Field

from .costs import RunLedger
from .mcp import StackQLServer
from .recon import ReconReport
from .tiers import make_agent, tier

DECIDE_MAX_TURNS = 12


@dataclass(frozen=True)
class Policy:
    elevated_rps: float
    tightened_threshold: int
    baseline_threshold: int
    period: int


class Decision(BaseModel):
    action: Literal["tighten", "hold"]
    new_threshold: int = Field(description="requests per period after this decision")
    rationale: str
    risk: str = Field(default="", description="what the change affects if the read is wrong")
    statement: str = Field(
        default="",
        description="on tighten: the single REPLACE statement to execute, discovered via describe_method; empty on hold",
    )
    verification_select: str = Field(
        default="",
        description="on tighten: a flat SELECT, validated, that returns the rule's requests_per_period after the change",
    )
    rollback_statement: str = Field(
        default="", description="on tighten: the same statement with the previous threshold"
    )


def policy_action(report: ReconReport, policy: Policy) -> tuple[str, int]:
    """The deterministic policy the model is asked to apply; code applies it too."""
    if (
        report.requests_per_second >= policy.elevated_rps
        and report.threshold > policy.tightened_threshold
    ):
        return "tighten", policy.tightened_threshold
    return "hold", report.threshold


def enforce_policy(decision: Decision, report: ReconReport, policy: Policy) -> Decision:
    """Code has the last word on what may be proposed to the gate. When the override changes the
    action or the threshold, the model's statements no longer describe the decision: they are
    dropped, and a tighten with no statement is reported by the gate as unexecutable."""
    action, threshold = policy_action(report, policy)
    if (decision.action, decision.new_threshold) == (action, threshold):
        return decision
    return decision.model_copy(
        update={
            "action": action,
            "new_threshold": threshold,
            "rationale": (
                f"{decision.rationale} [policy override: model proposed "
                f"{decision.action} -> {decision.new_threshold}; policy gives {action} -> {threshold}]"
            ),
            "statement": "",
            "verification_select": "",
            "rollback_statement": "",
        }
    )


async def run_decision(
    server: StackQLServer,
    instructions: str,
    report: ReconReport,
    policy: Policy,
    ledger: RunLedger,
) -> Decision:
    agent = make_agent(
        "reasoning",
        name="edge-decision",
        instructions=instructions,
        mcp_servers=[server],
        output_type=Decision,
    )
    prompt = json.dumps({"recon": report.model_dump(), "policy": asdict(policy)}, indent=2)
    result = await Runner.run(agent, prompt, max_turns=DECIDE_MAX_TURNS)
    ledger.record("decision", tier("reasoning").model, result)
    return enforce_policy(result.final_output_as(Decision), report, policy)
