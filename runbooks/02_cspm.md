# 02 - CSPM sweep (6 min)

## State required

Seeded estate (`make seed-check` green for aws, azure, gcp, github). Findings repo (`GITHUB_ISSUES_REPO`) exists with no open demo issues (`make reset`).

## Commands

```
make sweep-cspm                                   # live: 21 queries, escalation, issues, cost
uv run python -m oape_agents.sweeps.cspm --dry-run   # live but no issues
uv run python -m oape_agents.sweeps.cspm --fallback  # replay
```

## Talk track

- 0:00 Start the run, then explain what is running: `oape_agents/sweeps/cspm.py` is a spec - a list of query ids and a severity guide. The queries are files in `queries/cspm/`; open one (`aws_security_group_ingress_exposure.sql`) and read the header: id, providers, params, expected columns.
- 1:00 "The mini-tier model runs the queries through the read-only MCP server. It cannot see the mutation tools - they are filtered out of the tool list and the server is started in read_only mode. Both are enforced, not documented." Show `oape_agents/common/mcp.py` top comment.
- 2:00 Fan-out: the S3 check needs a bucket list, so the model renders the second query from the first's rows via `render_query`. The SQL still comes from the file; the model supplies values only.
- 3:00 Findings table appears (about 16 on the seeded estate: the planted public access block, open SSH group, admin user with an active key, unencrypted RDS, 16 regions without CloudTrail, the Azure storage account and NSG, the GCP bucket and firewall, the unprotected GitHub branch and the deploy key - plus whatever else is really in the account).
- 3:30 Escalation: findings at or above `ESCALATION_SEVERITY` go to the frontier model. Point at the correlation in the assessment: the S3 finding is graded down because account-level Block Public Access still applies; the admin key created today is still critical because age is not a mitigant.
- 4:30 Issues open in the findings repo, one per material finding, body carries evidence, the query id and the proposed StackQL statement marked "for review, not executed". Open one in the browser.
- 5:00 Cost table: two tiers, tool calls split SELECT versus mutation (zero mutations), USD total. Trace URL. "This runs hourly. Nobody typed a prompt."

## Where to show the trace and cost

At the end of the run the table prints itself. Click the trace URL; the spans show every MCP tool call with its SQL.

## Notes

- The sweep is account-wide for AWS, Azure and GCP; pre-existing findings in the demo tenancy appear alongside the planted ones (the build tenancy showed three more security groups open on 22 and three more IAM users with admin keys). Decide beforehand whether that is a feature of the story or something to scope out via `s3_bucket_prefix` style parameters.
- GitHub per-repo checks are scoped to repos with the demo prefix; say so.

## Fallback trigger

No first tool call within 60 s, provider errors, or the run passes 5 minutes: `--fallback`.
