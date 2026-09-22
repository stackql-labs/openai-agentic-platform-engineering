"""Entitlements audit: privileged principals across the clouds, GitHub org members against the
IdP, leavers still holding privilege. Output is a recertification-style report.

    uv run python -m oape_agents.sweeps.entitlements [--fallback] [--record]
"""

from __future__ import annotations

import sys

from oape_agents.sweeps.base import SweepSpec, cli

SPEC = SweepSpec(
    scenario="entitlements",
    title="Entitlements audit",
    query_ids=[
        "cspm/aws_iam_users",
        "cspm/aws_iam_user_attached_policies",
        "entitlements/privileged_principals_all_clouds",
        "cspm/github_repos",
        "cspm/github_outside_collaborators_admin",
        "entitlements/github_members_vs_idp",
        "entitlements/idp_groups",
        "entitlements/idp_privileged_group_members",
    ],
    classify_instructions=(
        "critical = a disabled (leaver) identity that still holds privilege (status 'leaver still "
        "privileged' or 'leaver: IdP account disabled'); high = a service account or non-human "
        "principal holding roles/owner, Owner or AdministratorAccess, and an outside collaborator "
        "with admin; medium = a GitHub member with no IdP identity (orphan) and a human holding "
        "Owner at subscription scope; info = entries whose status is 'ok' are NOT findings. "
        "Order: run cspm/aws_iam_users, then cspm/aws_iam_user_attached_policies for every user "
        "(iam_user_name as a JSON array), collect the users with AdministratorAccess and pass them as "
        "aws_admin_users when rendering entitlements/privileged_principals_all_clouds. Run the GitHub "
        "collaborator query only for repos starting with the demo prefix. Every row that "
        "entitlements/privileged_principals_all_clouds returns is a finding (one per principal, provider "
        "aws, azure or google, finding_type privileged_principal): service accounts and non-human "
        "principals high, humans medium. If an IdP query fails with a 403, record one info finding "
        "'idp_access_blocked' with provider entra_id and continue."
    ),
    escalate_instructions=(
        "This is a recertification: for each escalated principal say who should confirm it, what "
        "the least-privilege alternative is, and give the StackQL statement that would remove the "
        "grant (DELETE role assignment, REPLACE IAM policy without the member, DELETE collaborator)."
    ),
    artifact="report",
    context_notes=[
        "The IdP is Microsoft Entra ID. If the app registration lacks Graph consent the IdP queries "
        "return 403; the join then reports every GitHub member as an orphan - say that the IdP was "
        "unreachable rather than reporting orphans as findings.",
    ],
)

if __name__ == "__main__":
    sys.exit(cli(SPEC))
