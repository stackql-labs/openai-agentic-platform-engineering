# openai-agentic-platform-engineering
# Thin wrapper over each use case's own toolchain. Every target runs from the repo root and every
# process reads the repo-root .env. See README.md for what each use case does.

SHELL := bash
.DEFAULT_GOAL := help
USE_CASES := finops entitlements sre drift edge
STACKQL_DEPLOY ?= stackql-deploy

help: ## list targets
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-24s %s\n", $$1, $$2}'

env: ## create .env from .env.example if it does not exist
	@test -f .env || (cp .env.example .env && echo "created .env from .env.example - fill it in")

# --- setup: install deps and pull the StackQL providers each use case needs ----------------
setup: env setup-finops setup-entitlements setup-sre setup-drift setup-edge ## everything

setup-finops: ## python: uv sync, pull aws/azure/google, verify server_info
	cd finops && uv sync && uv run python -m finops setup

setup-entitlements: ## typescript: npm install, pull entra_id/okta/aws/azure/google/github
	cd entitlements && npm install && npm run setup

setup-sre: ## rust: cargo build, pull k8s
	cd sre && cargo build --release && ./target/release/sre setup

setup-drift: ## javascript: npm install, pull aws/azure
	cd drift && npm install
	node drift/src/cli.js setup

setup-edge: ## python: uv sync, pull cloudflare/github
	cd edge && uv sync && uv run python -m edge setup

# --- demo estates: one stackql-deploy stack per use case (per provider where multi-cloud) --
stack-finops: ## deploy the finops estate (aws, azure, google sub-stacks)
	$(STACKQL_DEPLOY) build finops/stack/aws dev --env-file .env
	$(STACKQL_DEPLOY) build finops/stack/azure dev --env-file .env
	$(STACKQL_DEPLOY) build finops/stack/google dev --env-file .env

stack-entitlements: ## deploy the entitlements estate (aws, entra_id sub-stacks)
	$(STACKQL_DEPLOY) build entitlements/stack/aws dev --env-file .env
	$(STACKQL_DEPLOY) build entitlements/stack/entra_id dev --env-file .env

stack-sre: ## deploy the sre estate into the kind cluster (needs kubectl proxy on KUBE_CLUSTER_ADDR)
	$(STACKQL_DEPLOY) build sre/stack dev --env-file .env

stack-drift: ## deploy the drift estate (aws, azure sub-stacks)
	$(STACKQL_DEPLOY) build drift/stack/aws dev --env-file .env
	$(STACKQL_DEPLOY) build drift/stack/azure dev --env-file .env

stack-edge: ## deploy the edge estate (cloudflare rate limit ruleset)
	$(STACKQL_DEPLOY) build edge/stack dev --env-file .env

teardown-finops: ## remove the finops estate
	$(STACKQL_DEPLOY) teardown finops/stack/aws dev --env-file .env
	$(STACKQL_DEPLOY) teardown finops/stack/azure dev --env-file .env
	$(STACKQL_DEPLOY) teardown finops/stack/google dev --env-file .env

teardown-entitlements: ## remove the entitlements estate
	$(STACKQL_DEPLOY) teardown entitlements/stack/aws dev --env-file .env
	$(STACKQL_DEPLOY) teardown entitlements/stack/entra_id dev --env-file .env

teardown-sre: ## remove the sre estate from the kind cluster
	$(STACKQL_DEPLOY) teardown sre/stack dev --env-file .env

teardown-drift: ## remove the drift estate
	$(STACKQL_DEPLOY) teardown drift/stack/aws dev --env-file .env
	$(STACKQL_DEPLOY) teardown drift/stack/azure dev --env-file .env

teardown-edge: ## remove the edge estate (resets the rate limit phase)
	$(STACKQL_DEPLOY) teardown edge/stack dev --env-file .env

# --- run once (each README shows the always-on trigger) ------------------------------------
finops: ## FinOps sweep: idle resources across aws/azure/google -> cost report
	cd finops && uv run python -m finops run

entitlements: ## entitlements audit: privileged principals + IdP joins -> recertification report
	cd entitlements && npm run sweep

sre-alert: ## fire the synthetic incident (event trigger, writes runs/alert.json)
	cd sre && ./target/release/sre alert

sre: ## agentic SRE: diagnose -> propose -> approval gate -> one mutation -> verify
	cd sre && ./target/release/sre run

drift: ## drift briefing: snapshot -> SQL delta -> brief on deltas only
	node drift/src/cli.js run

edge: ## edge autopilot: recon -> decision -> approval gate -> rate limit REPLACE -> decision record
	cd edge && uv run python -m edge run

# --- checks -----------------------------------------------------------------------------------
test: ## every use case's offline tests, lint and typecheck
	cd finops && uv run ruff check . && uv run pytest -q
	cd entitlements && npm run typecheck && npm test
	cd sre && cargo test --quiet
	cd drift && npm test
	cd edge && uv run ruff check . && uv run pytest -q

.PHONY: help env setup $(addprefix setup-,$(USE_CASES)) $(addprefix stack-,$(USE_CASES)) $(addprefix teardown-,$(USE_CASES)) $(USE_CASES) sre-alert test
