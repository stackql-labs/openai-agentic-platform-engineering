"""FinOps sweep: idle and orphaned resources across AWS, Azure and GCP with monthly estimates.

uv run python -m oape_agents.sweeps.finops [--fallback] [--dry-run] [--record]
"""

from __future__ import annotations

import sys

from oape_agents.sweeps.base import SweepSpec, cli

SPEC = SweepSpec(
    scenario="finops",
    title="FinOps sweep",
    query_ids=[
        "finops/aws_unattached_volumes",
        "finops/aws_unassociated_eips",
        "finops/aws_stale_snapshots",
        "finops/azure_unattached_disks",
        "finops/azure_unassociated_public_ips",
        "finops/azure_stale_snapshots",
        "finops/google_unattached_disks",
        "finops/google_stale_snapshots",
    ],
    classify_instructions=(
        "severity follows the estimated monthly waste in est_monthly_usd: high when at least 50 USD, "
        "medium when at least 5 USD, low otherwise; info for anything with no estimate. Always copy "
        "est_monthly_usd into monthly_cost_estimate_usd. finding_type is one of unattached_volume, "
        "unassociated_ip, stale_snapshot. Resource is the volume/disk/address id or name. "
        "proposed_remediation is the StackQL DELETE (or EXEC release) statement for that resource. "
        "Report every row: FinOps findings are individually small and add up."
    ),
    escalate_instructions=(
        "Group the waste by provider and total it. Material means the resource is safe to delete now: "
        "unattached for more than a day, carrying the demo tag or no owner tag, no snapshot lineage "
        "needed. Draft the remediation as a batch of StackQL DELETE statements ordered by savings."
    ),
    artifact="issues",
    escalation_mode="batch",
    context_notes=[
        "Monthly estimates are list-price approximations computed in SQL; label them as estimates.",
    ],
)

if __name__ == "__main__":
    sys.exit(cli(SPEC))
