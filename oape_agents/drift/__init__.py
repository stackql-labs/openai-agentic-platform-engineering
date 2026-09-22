"""Drift briefing: StackQL materializes the estate into a local SQLite backend (materialized
views), snapshots are normalised into comparable rows, deltas and the tfstate comparison are plain
SQL over that backend, and a mini-tier agent briefs on the deltas only."""
