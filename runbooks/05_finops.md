# 05 - FinOps sweep (4 min)

## State required

Seeded estate: unattached EBS volume, unassociated EIP, Azure orphan disk and public IP, GCP orphan disk. Stale snapshots are whatever really exists in the tenancy.

## Commands

```
make sweep-finops                                  # live, about 1 min
uv run python -m oape_agents.sweeps.finops --fallback
```

## Talk track

- 0:00 Start the run. "Same loop, the question is money. Every row carries a monthly estimate computed in SQL from list prices."
- 0:30 Open `queries/finops/aws_unattached_volumes.sql`: the CASE that prices gp3 versus gp2. Open `google_unattached_disks.sql`: same shape, different provider. "Cross-cloud waste in one findings schema."
- 1:15 Findings table with the est. USD/mo column. Point at the planted resources and the real ones.
- 1:45 Batch escalation: every FinOps finding goes to the frontier model together; it returns one assessment per provider and the sweep files one issue per provider with the total, the itemised list and a batch of DELETE statements ordered by savings. Open the issue.
- 2:45 Cost table: measured 2 requests, 8 SELECTs, USD 0.05, 55 s on the mini tier before escalation. "A sweep that costs cents and finds dollars."

## Fallback trigger

No first tool call within 60 s or the run passes 3 minutes: `--fallback`.
