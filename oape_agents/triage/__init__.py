"""Gated incident triage: the one scenario that mutates.

alert.py  - the synthetic trigger (script/webhook stub) that writes runs/alert.json
triage.py - diagnose (SELECTs only) -> propose -> gate -> execute -> verify -> close
gate.py   - the approval gate and the ONLY code path that can reach run_mutation_query /
            run_lifecycle_operation (see tests/test_gate.py)
"""
