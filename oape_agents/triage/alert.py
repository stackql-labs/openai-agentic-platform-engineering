"""Synthetic alert - the event trigger for the triage scenario.

    uv run python -m oape_agents.triage.alert [--symptom "..."]

Writes runs/alert.json in the shape a webhook receiver would hand to the agent. In production
this is a PagerDuty/CloudWatch/Azure Monitor payload; here it is a script so the demo has a
deterministic trigger that is not a human typing a prompt.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel

from oape_agents.common.config import RUNS_DIR, settings
from oape_agents.common.costs import console

ALERT_PATH = RUNS_DIR / "alert.json"


class Alert(BaseModel):
    alert_id: str
    fired_at: str
    source: str
    service: str
    severity: str
    symptom: str
    signal: str
    scope: dict[str, str]


def fire(symptom: str | None = None) -> Alert:
    s = settings()
    a = Alert(
        alert_id=f"alrt-{uuid.uuid4().hex[:8]}",
        fired_at=datetime.now(UTC).isoformat(),
        source="synthetic-monitor",
        service="oape-checkout",
        severity="page",
        symptom=symptom
        or "checkout p95 latency 2.4s against a 500ms SLO for 10 minutes; error rate rising",
        signal="capacity: request queue depth growing on the only in-service instance",
        scope={
            "aws_account_id": s.aws_account_id,
            "aws_region": s.aws_region,
            "auto_scaling_group": f"{s.demo_prefix}-checkout-asg",
            "github_repo": f"{s.github_org}/{s.demo_prefix}-checkout",
        },
    )
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ALERT_PATH.write_text(a.model_dump_json(indent=2), encoding="utf-8")
    return a


def load() -> Alert | None:
    if not ALERT_PATH.exists():
        return None
    return Alert.model_validate_json(ALERT_PATH.read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symptom", default=None)
    a = ap.parse_args()
    alert = fire(a.symptom)
    console.rule("[bold]alert fired[/bold]")
    console.print(json.dumps(alert.model_dump(), indent=2))
    console.print(f"written to {ALERT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
