# 06 - Drift briefing (4 min)

## State required

`make tf-apply` done once (produces `seed/terraform/terraform.tfstate`). The Terraform subset is UNperturbed (`make reset` restores it) and a snapshot pair already exists from the pre-demo checklist (`make drift` run twice, so the first live run of the segment reports no change).

## Commands

```
make drift                        # 1st run: snapshot + delta + tfstate comparison -> "no changes", no model call
make tf-perturb                   # out-of-band change: instance tag env -> staging, tcp/8080 from 0.0.0.0/0 on the TF security group
make drift                        # 2nd run: two deltas, two drifts, brief on the deltas only
uv run python -m oape_agents.drift.brief --fallback     # replay of the perturbed run
```

## Talk track

- 0:00 `make drift`. While it runs: "StackQL materializes the estate into a local SQLite backend - `CREATE MATERIALIZED VIEW snap_<ts>_aws_security_groups AS SELECT ...`. Five sources, one file, `snapshots/estate.db`." Show `queries/drift/`.
- 0:40 It finishes with one line: no changes since the previous snapshot, no drift from Terraform, zero model requests, USD 0.0000. "This is most hours. The model is not even called."
- 1:00 "The delta is SQL over that file, not over the cloud." Open `queries/drift/delta.sql`: added, removed, changed on a normalised attribute JSON. "The Terraform state is read locally by `terraform_state_reader.py` into the same backend; `tfstate_vs_live.sql` is the comparison."
- 1:30 `make tf-perturb`. Two out-of-band changes in five seconds: a tag, and a new ingress rule.
- 1:45 `make drift` again. Deltas print: the tag change and the 0.0.0.0/0 rule, both as snapshot deltas and as drift from intended state.
- 2:30 The brief: the mini-tier model saw only those rows. Tag churn classified benign, the 8080 rule material, one paragraph with the resource ids.
- 3:00 Cost table: one request, under a thousand input tokens, USD 0.002, four seconds. "Reasoning on deltas rather than the full estate is what makes hourly runs economical."

## Fallback trigger

Snapshot errors or the run passes 3 minutes: `--fallback` (recorded with the two planted perturbations).

## After the demo

`make reset` restores the terraform-managed resources. Run `make drift` twice before the next rehearsal so the first live run reports no change again.
