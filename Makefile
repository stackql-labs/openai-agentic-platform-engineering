# openai-agentic-platform-engineering
# Every target runs with the repo's .env (python-dotenv loads it; stackql reads it via --env.file).
# Windows: run from Git Bash or WSL (GNU make + bash). uv manages the Python environment.

SHELL := bash
export PYTHONUTF8 := 1
export PYTHONIOENCODING := utf-8
.DEFAULT_GOAL := help
UV ?= uv
PY := $(UV) run python
STACKQL ?= stackql
APPROOT ?= .stackql
PROVIDERS := aws azure google github openai_admin entra_id okta

help: ## list targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-22s %s\n", $$1, $$2}'

# --- phase 0 ------------------------------------------------------------------
setup: ## install deps (uv), pull StackQL providers into ./.stackql, verify MCP server_info
	$(UV) sync --extra dev
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example - fill it in")
	@mkdir -p $(APPROOT) runs snapshots
	@for p in $(PROVIDERS); do $(STACKQL) exec --approot $(APPROOT) "REGISTRY PULL $$p" >/dev/null && echo "pulled $$p"; done
	$(PY) -m oape_agents.tools.verify_mcp

smoke: ## smoke agent: list providers + one SELECT per configured provider (live)
	$(PY) -m oape_agents.smoke

lint: ## ruff check + format check
	$(UV) run ruff check .
	$(UV) run ruff format --check .

fmt: ## ruff format
	$(UV) run ruff format .

test: ## unit tests (gate unreachability, schema, query loader) - no live APIs
	$(UV) run pytest -q -m "not live"

# --- phase 1: demo estate -------------------------------------------------------
seed: ## build the deliberately misconfigured demo estate (tagged purpose=oape-demo)
	$(PY) -m seed.run seed

teardown: ## destroy everything tagged purpose=oape-demo (idempotent, tag-filtered)
	$(PY) -m seed.run teardown

seed-check: ## checklist query per planted misconfiguration - each must return its row
	$(PY) -m seed.run check

seed-status: ## what exists right now, per provider
	$(PY) -m seed.run status

tf-apply: ## apply the terraform-managed subset (produces tfstate for the drift scenario)
	cd seed/terraform && terraform init -input=false >/dev/null && terraform apply -auto-approve -input=false

tf-perturb: ## perturb the terraform-managed resources out of band (tag change + attribute drift)
	$(PY) -m seed.terraform.perturb

tf-destroy: ## destroy the terraform-managed subset
	cd seed/terraform && terraform destroy -auto-approve -input=false

# --- phase 2: query library -----------------------------------------------------
validate-queries: ## run validate_select_query / dryrun across queries/ and check expected columns
	$(PY) -m oape_agents.tools.validate_queries

# --- phase 3: sweeps ------------------------------------------------------------
sweep-cspm: ## CSPM sweep (read-only) -> GitHub issues + console brief + cost
	$(PY) -m oape_agents.sweeps.cspm

sweep-entitlements: ## entitlements audit (read-only) -> recertification report + cost
	$(PY) -m oape_agents.sweeps.entitlements

sweep-finops: ## FinOps sweep (read-only) -> GitHub issues + console brief + cost
	$(PY) -m oape_agents.sweeps.finops

schedule: ## run all three sweeps on the SWEEP_SCHEDULE_MINUTES interval (ctrl-c to stop)
	$(PY) -m oape_agents.scheduler

# --- phase 4: gated triage ------------------------------------------------------
alert: ## fire the synthetic alert (writes runs/alert.json)
	$(PY) -m oape_agents.triage.alert

triage: ## diagnose -> propose -> approval gate -> mutate -> verify (the one mutation)
	$(PY) -m oape_agents.triage.triage

triage-reset: ## put the triage target back to its pre-incident state
	$(PY) -m oape_agents.triage.triage --reset

# --- phase 5: drift ---------------------------------------------------------------
snapshot: ## materialize the estate into the local backend (snapshots/estate.db)
	$(PY) -m oape_agents.drift.snapshot

drift: ## delta brief: current vs prior snapshot, plus tfstate comparison
	$(PY) -m oape_agents.drift.brief

# --- phase 6: closer ----------------------------------------------------------------
openai-estate: ## governance brief over the OpenAI org (projects, service account keys, spend)
	$(PY) -m oape_agents.openai_estate.estate

# --- phase 7: rehearsal -------------------------------------------------------------
rehearse: ## full demo running order against fallbacks, no live APIs
	$(PY) -m oape_agents.rehearse

record-fallbacks: ## re-record fallbacks from live runs (needs seeded estate)
	$(PY) -m oape_agents.rehearse --record

reset: ## close demo issues, reset triage target, clear runs/ - between rehearsals
	$(PY) -m oape_agents.tools.reset

.PHONY: help setup smoke lint fmt test seed teardown seed-check seed-status tf-apply tf-perturb tf-destroy validate-queries sweep-cspm sweep-entitlements sweep-finops schedule alert triage triage-reset snapshot drift openai-estate rehearse record-fallbacks reset
