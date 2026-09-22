"""Seed and teardown of the deliberately misconfigured demo estate.

Every resource carries the demo tag (purpose=oape-demo) and the demo name prefix. Teardown is
idempotent and tag-filtered: it discovers what exists by tag, verifies the tag on each target,
and deletes only that. Run `python -m seed.run seed|teardown|check|status [--only aws,azure,...]`.
"""
