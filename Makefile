# Revlane: every command the project needs. See CLAUDE.md.

# Behind a TLS-inspecting proxy uv must trust the system certificate store.
export UV_SYSTEM_CERTS ?= 1

# Machine-local overrides, not committed. Example: a machine whose security
# policy stops compiled libraries loading from this folder can keep the
# virtualenv elsewhere:  export UV_PROJECT_ENVIRONMENT := $(HOME)/.cache/revlane/venv-api
-include .make.local

API := apps/api
WEB := apps/web

.DEFAULT_GOAL := help
.PHONY: help install db dev migrate lint test e2e eval eval-live synth ingest audit agent worker demo retention offboard user

help:
	@echo "make demo      load a small synthetic merchant, run the audit, and start everything"
	@echo "make install   install API and web dependencies, and the e2e browser"
	@echo "make dev       start Postgres, the API (:8000) and the web app (:3000)"
	@echo "make migrate   apply database migrations"
	@echo "make lint      ruff, mypy, eslint, tsc"
	@echo "make test      backend and frontend unit tests"
	@echo "make e2e       Playwright tests"
	@echo "make eval      agent and chat evaluation suite, in stub mode"
	@echo "make eval-live the chat question set against the live model (needs LLM_API_KEY)"
	@echo "make synth     generate the synthetic merchant into data/synth/"
	@echo "make ingest    load data/synth/ (or ARGS=<folder>) into the demo tenant"
	@echo "make audit     run the matching and fee engines for the demo tenant"
	@echo "make agent     run one playbook now: make agent ARGS=leakage_audit"
	@echo "make worker    carry out queued agent runs"
	@echo "make user      add a person or set a password: make user ARGS=\"add a@b.c 'A Name' approver\""
	@echo "make retention delete records older than each tenant's retention period"
	@echo "make offboard  delete a tenant's credentials: make offboard ARGS=<tenant id>"

install:
	cd $(API) && uv sync
	cd $(WEB) && npm ci
	cd $(WEB) && npx playwright install chromium

# Docker Compose when Docker is installed; otherwise a Postgres already running locally.
db:
	@if command -v docker >/dev/null 2>&1; then \
		docker compose up -d --wait db; \
	else \
		./scripts/local-db.sh; \
	fi

migrate:
	cd $(API) && uv run alembic upgrade head

# Other ports, when 8000 or 3000 are taken: make dev API_PORT=8100 WEB_PORT=3100
API_PORT ?= 8000
WEB_PORT ?= 3000
dev: db migrate
	@echo "API on http://localhost:$(API_PORT), console on http://localhost:$(WEB_PORT)"
	@trap 'kill 0' INT TERM EXIT; \
	(cd $(API) && WEB_ORIGINS='["http://localhost:$(WEB_PORT)","http://127.0.0.1:$(WEB_PORT)"]' \
		uv run uvicorn revlane.main:app --reload --host 127.0.0.1 --port $(API_PORT)) & \
	(cd $(API) && uv run python -m revlane.harness worker) & \
	(cd $(WEB) && NEXT_PUBLIC_API_URL=http://localhost:$(API_PORT)/api/v1 \
		npm run dev -- --port $(WEB_PORT)) & \
	wait

lint:
	cd $(API) && uv run ruff check . && uv run ruff format --check . && uv run mypy
	cd $(WEB) && npm run lint && npm run typecheck

test:
	cd $(API) && uv run pytest tests
	cd $(WEB) && npm test

e2e:
	cd $(WEB) && npm run e2e

eval:
	cd $(API) && LLM_STUB=true uv run pytest evals

# The chat question set against the live model. Needs LLM_API_KEY; sends only
# synthetic data. Reported separately from `make eval` and never part of it.
eval-live:
	cd $(API) && LLM_STUB=false uv run pytest evals -k "chat_with_the_live_model"

# Extra options go in ARGS, for example: make synth ARGS="--scale 0.05 --as-of 2026-11-16"
synth:
	cd $(API) && uv run python -m revlane.synth $(ARGS)

# Loads the synthetic merchant by default. Another folder: make ingest ARGS="path/to/folder"
ingest: migrate
	cd $(API) && uv run python -m revlane.ingest $(ARGS)

# Matches orders, payments, settlements and bank credits, checks fees against the
# contracts, and stores the findings. Example: make audit ARGS="--as-of 2026-11-16"
audit: migrate
	cd $(API) && uv run python -m revlane.audit $(ARGS)

# Runs one playbook for the demo tenant with the stub model unless a key is set.
# Playbooks: leakage_audit, reconciliation, fee_audit.
agent: migrate
	cd $(API) && uv run python -m revlane.harness run $(ARGS)

# Carries out runs queued through the API. Add ARGS=--once to stop when the queue is empty.
worker: migrate
	cd $(API) && uv run python -m revlane.harness worker $(ARGS)

# From a fresh clone to a working demo: the database, a small synthetic merchant
# (about a twentieth of full size, dated today), the audit, then the API, the
# worker and the web app. A tenant that already holds data is left as it is.
DEMO_SCALE ?= 0.05
demo: db migrate
	cd $(API) && uv run python -m revlane.demo --scale $(DEMO_SCALE)
	$(MAKE) dev

# Lifecycle jobs. See docs/RUNBOOK.md.
retention: migrate
	cd $(API) && uv run python -m revlane.lifecycle retention

offboard: migrate
	cd $(API) && uv run python -m revlane.lifecycle offboard $(ARGS)

# People who can sign in. The password is asked for at the prompt.
#   make user ARGS="add someone@example.com 'Their Name' approver"
#   make user ARGS="password someone@example.com"
user: migrate
	cd $(API) && uv run python -m revlane.auth $(ARGS)
