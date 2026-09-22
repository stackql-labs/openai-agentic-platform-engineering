# 03 - Entitlements audit (4 min)

## State required

Seeded estate. Entra ID Graph consent granted if the IdP join is to run live (see WORK_ORDER.md); without it the sweep records one "idp_access_blocked" finding and the runbook says so.

## Commands

```
make sweep-entitlements                                    # live, about 2 min
uv run python -m oape_agents.sweeps.entitlements --fallback
```

## Talk track

- 0:00 Start the run. "Same pattern, different question: who holds the highest privilege in each cloud, and does every GitHub member map to a person the IdP still knows?"
- 0:45 Open `queries/entitlements/privileged_principals_all_clouds.sql`: one statement, three providers, one result set. AWS admins are found first (attached policies per user, one SELECT per user unioned), then rendered into the cross-cloud query as a literal list. Azure Owner assignments at subscription scope and GCP roles/owner bindings are live joins.
- 1:30 Open `github_members_vs_idp.sql`: GitHub org members LEFT JOIN IdP users. Orphans have no identity, leavers have a disabled one. If Graph consent is not granted, the finding says the IdP was unreachable rather than inventing orphans.
- 2:00 Findings: the planted service account with roles/owner, the admin IAM users, the human Owners at subscription scope (a standing-privilege finding, not an incident).
- 2:45 The artifact is a recertification report, not issues: `runs/entitlements-recertification-<ts>.md`. Open it - one table an owner can sign, evidence and the revoking StackQL statement per line.
- 3:30 Cost table and trace. Measured: 20 requests, USD 0.17, 112 s.

## Fallback trigger

Provider errors other than the documented Entra 403, or the run passes 3 minutes: `--fallback`.
