"""Decision step (frontier tier, no tools). Given the recon report and the threshold policy from
.env it returns a structured decision. The model supplies the judgement and the rationale; code
enforces the policy envelope (only the two configured thresholds can ever be proposed) and
renders the rollback statement from the query file - the model never authors SQL."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal

from agents import Runner
from pydantic import BaseModel, Field

from .config import settings
from .costs import RunLedger
from .queries import render_query
from .recon import ReconReport
from .tiers import make_agent, tier

DECIDE_INSTRUCTIONS = """You decide whether to tighten one Cloudflare rate limiting rule.

You have no tools. You get a recon report (live zone analytics over a short window plus the
current rule) and a policy. Apply the policy exactly:

- tighten when requests_per_second >= elevated_rps AND the current threshold is greater than
  tightened_threshold; then new_threshold = tightened_threshold
- otherwise hold, with new_threshold = the current threshold

Write a rationale of two to four sentences that cites the numbers (requests per second against
the elevated_rps line, the non-2xx share, country concentration, current threshold and period)
and a one or two sentence risk note: what a block at the new threshold would affect and what
would be wrong if the traffic is legitimate. Matter of fact, no adjectives."""


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


def policy_action(report: ReconReport, policy: Policy) -> tuple[str, int]:
    """The deterministic policy the model is asked to apply; code applies it too."""
    if (
        report.requests_per_second >= policy.elevated_rps
        and report.threshold > policy.tightened_threshold
    ):
        return "tighten", policy.tightened_threshold
    return "hold", report.threshold


def enforce_policy(decision: Decision, report: ReconReport, policy: Policy) -> Decision:
    """Code has the last word on what may be proposed to the gate."""
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
        }
    )


def rollback_statement(previous_threshold: int) -> str:
    """The statement that puts the previous threshold back, rendered from the query file."""
    return render_query(
        "edge/tighten_rate_limit",
        threshold=previous_threshold,
        demo_prefix=settings().demo_prefix,
    )


async def run_decision(report: ReconReport, policy: Policy, ledger: RunLedger) -> Decision:
    agent = make_agent(
        "reasoning", name="edge-decision", instructions=DECIDE_INSTRUCTIONS, output_type=Decision
    )
    prompt = json.dumps({"recon": report.model_dump(), "policy": asdict(policy)}, indent=2)
    result = await Runner.run(agent, prompt, max_turns=2)
    ledger.record("decision", tier("reasoning").model, result)
    return enforce_policy(result.final_output_as(Decision), report, policy)
