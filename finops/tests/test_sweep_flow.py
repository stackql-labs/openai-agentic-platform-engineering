"""The orchestration end to end with the model and the server replaced: the prompts are
rendered from finops/prompts/ with the discovery briefing and server instructions appended,
the sweep input is a trigger (no SQL), the sweep tier's FindingSet is normalised and printed,
every provider with findings gets one reasoning batch, the markdown report and the run record
are written, and the ledger counts select calls only."""

from __future__ import annotations

import json
from types import SimpleNamespace

from agents import Usage
from agents.items import ToolCallItem

from finops import costs, report, sweep
from finops.findings import DeferredItem, Finding, FindingSet, ProposedDeletion, ProviderPlan


def _finding(provider, resource, cost):
    return Finding(
        provider=provider,
        resource=resource,
        finding_type="unattached_volume",
        severity="critical",  # wrong on purpose: normalise() re-applies the cost rule
        title=f"{resource} idle",
        evidence="row",
        proposed_remediation="DELETE ...",
        query_id=f"finops/{provider}_unattached",
        monthly_cost_estimate_usd=cost,
    )


class FakeResult:
    def __init__(self, agent, final_output, selects):
        self.final_output = final_output
        self.new_items = [
            ToolCallItem(agent=agent, raw_item=SimpleNamespace(name="run_select_query"))
            for _ in range(selects)
        ]
        self.context_wrapper = SimpleNamespace(
            usage=Usage(requests=selects + 1, input_tokens=1000, output_tokens=100)
        )


async def test_run_sweep_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(report, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(costs, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(sweep, "assert_read_only", lambda server: None)

    class FakeServer:
        """Stands in for MCPServerStdio: its own async context manager, with read_resource."""

        def __init__(self, name):
            self.name = name

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def read_resource(self, uri):
            assert uri == "stackql://docs/instructions"
            return SimpleNamespace(contents=[SimpleNamespace(text="SERVER GUIDANCE")])

    monkeypatch.setattr(sweep, "read_only_server", FakeServer)
    calls = []

    async def fake_run(agent, input, max_turns):
        calls.append((agent.name, agent.model, input, agent.instructions))
        if agent.output_type is FindingSet:
            return FakeResult(
                agent,
                FindingSet(
                    findings=[_finding("aws", "vol-1", 0.1), _finding("azure", "disk-1", 60)],
                    summary="Two idle resources.",
                ),
                selects=8,
            )
        provider = agent.name.rsplit("-", 1)[1]
        return FakeResult(
            agent,
            ProviderPlan(
                provider="wrong",  # code pins the provider from the batch
                rationale="tagged and idle",
                proposed=[
                    ProposedDeletion(
                        resource="x",
                        finding_type="unattached_volume",
                        monthly_cost_estimate_usd=1.0,
                        statement=f"DELETE FROM {provider}",
                        reason="idle",
                    )
                ],
                deferred=[DeferredItem(resource="y", reason="recent")],
            ),
            selects=1,
        )

    monkeypatch.setattr(sweep.Runner, "run", fake_run)
    assert await sweep.run_sweep(["aws", "azure"]) == 0

    assert [c[0] for c in calls] == [
        "finops-sweep",
        "finops-reasoning-aws",
        "finops-reasoning-azure",
    ]
    assert calls[0][1] == "test-sweep-model" and calls[1][1] == "test-reasoning-model"
    # the sweep input is a trigger, not a query pack; the instructions are the rendered prompts
    assert "Providers in scope: aws, azure." in calls[0][2] and "SELECT" not in calls[0][2].upper()
    sweep_instr = calls[0][3]
    assert "{{" not in sweep_instr
    assert (
        "- aws: region ap-southeast-2" in sweep_instr
        and "google" not in sweep_instr.split("## Output")[0].split("## Tenancy")[1]
    )
    assert "query_library_search" in sweep_instr and "SERVER GUIDANCE" in sweep_instr
    assert sweep_instr.index("## Working with StackQL") < sweep_instr.index("SERVER GUIDANCE")
    reasoning_instr = calls[1][3]
    assert "(aws)" in reasoning_instr and "describe_method" in reasoning_instr
    assert (
        "- aws: region ap-southeast-2" in reasoning_instr
        and "- azure: subscription" not in reasoning_instr
    )
    assert "SERVER GUIDANCE" in reasoning_instr
    assert '"fingerprint"' in calls[1][2] and "disk-1" not in calls[1][2]

    md = next(tmp_path.glob("finops-*.md")).read_text()
    js = json.loads(next(tmp_path.glob("finops-*.json")).read_text())
    assert "DELETE FROM aws" in md and "DELETE FROM azure" in md and "not executed" in md
    assert [p["provider"] for p in js["plans"]] == ["aws", "azure"]
    sev = {f["resource"]: f["severity"] for f in js["findings"]["findings"]}
    assert sev == {"vol-1": "low", "disk-1": "high"}
    assert [e["label"] for e in js["entries"]] == [
        "sweep (discover + classify)",
        "reasoning (aws)",
        "reasoning (azure)",
    ]
    assert js["entries"][0]["select_calls"] == 8
    assert all(e["mutation_calls"] == 0 for e in js["entries"])
    assert js["trace_url"].startswith("https://platform.openai.com/traces/trace?trace_id=")
