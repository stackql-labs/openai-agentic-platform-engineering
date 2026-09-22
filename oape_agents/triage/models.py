"""Structured outputs for the triage loop. The proposal names one of a fixed menu of small,
reversible actions; the gate turns it into exactly one library statement."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CapacityObservation(BaseModel):
    desired_capacity: int
    min_size: int
    max_size: int
    in_service: int
    healthy_targets: int
    unhealthy_targets: int


class Diagnosis(BaseModel):
    summary: str = Field(description="Three to five sentences, matter of fact")
    capacity: CapacityObservation
    recent_deployments: list[str] = Field(
        description="One line per deployment in the window, e.g. 'production 1.4.2 ... 0.6h ago'"
    )
    evidence: list[str] = Field(description="Query ids run and the key values each returned")
    hypothesis: str = Field(description="Most likely cause given the evidence")


class Proposal(BaseModel):
    action: Literal["scale_out_by_one", "no_action"]
    target: str = Field(description="The exact resource the mutation touches")
    desired_capacity: int = Field(description="The new desired capacity if scaling out")
    rationale: str
    blast_radius: str = Field(description="What else changes, and what does not")
    rollback: str = Field(description="How to reverse it - the same statement with the old value")
    expected_verification: str = Field(
        description="What the post-mutation SELECT must show for the loop to close"
    )


class Closeout(BaseModel):
    outcome: Literal["recovered", "not_recovered", "declined", "no_action"]
    summary: str = Field(description="One paragraph incident note for the on-call handover")
