# 04 - Gated incident triage (6 min) - the one mutation

## State required

Checkout ASG at desired=1 with one healthy target (`make seed-status`). If a previous rehearsal scaled it out: `make triage-reset` and wait for the second instance to terminate (about a minute).

## Commands

```
make alert                        # fire the synthetic alert (writes runs/alert.json)
make triage                       # diagnose -> propose -> gate -> execute -> verify -> close
uv run python -m oape_agents.triage.triage --decline    # rehearsal of the abort path
uv run python -m oape_agents.triage.triage --fallback   # replay
make triage-reset                 # after the demo
```

## Talk track

- 0:00 `make alert`. Read the payload: a synthetic monitor page, service oape-checkout, latency SLO breach, queue depth on the only in-service instance. "This is the event. Nobody typed a prompt."
- 0:30 `make triage`. "The frontier model investigates with SELECTs only. It is on the same read-only server as the sweeps." Queries: ASG state, target health for the checkout target group, instances tagged for the service, deployments in the last 72 hours from GitHub.
- 1:30 Diagnosis prints: desired 1, in service 1, one healthy target, a production deployment of checkout 1.4.2 within the window. Hypothesis follows from the evidence.
- 2:00 Proposal prints: scale out by one, the exact target, the new desired capacity, blast radius, the rollback statement (the same UPDATE with the old value), what the verification SELECT must show. "The menu is fixed in code: scale out by one or no action."
- 2:30 The gate. The statement is on screen, rendered from `queries/triage/aws_asg_scale_out.sql`. Type the phrase. Before anything executes the gate runs a SELECT to assert the demo tag on the target; without the tag it refuses.
- 3:00 One `run_mutation_query` call. Show the audit log line: the SQL, decision, timestamp.
- 3:15 Verify loop: desired=2 in_service=1, then in_service=2 about 15 s later. Close-out note prints.
- 4:00 Cost table: diagnose, propose, execute (0 model requests, 1 mutation), close-out on the mini tier. Measured USD 0.15, 63 s. Trace URL.
- 4:30 Say what makes this survivable in a security review: read-only servers everywhere else, the mutation tools hidden from every model, the one executable statement rendered from a file, the tag assertion, the terminal approval, the audit log. `tests/test_gate.py` proves the first three.

## The decline path

If time allows, run `--decline`: the same diagnosis and proposal, then "declined - no mutation will run", zero mutation calls in the cost table.

## Fallback trigger

No first tool call within 60 s, or the ASG does not reach in_service=2 within 4 minutes: `--fallback`. The recorded run has the full loop including the audit response.

## After the demo

`make triage-reset` scales back to 1. This is operator tooling (boto3), not the agent path, and it checks the demo tag too.
