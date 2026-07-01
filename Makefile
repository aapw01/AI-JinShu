SHELL := /bin/bash
DEV_DATABASE_URL ?= postgresql://novel:novel_secret@localhost:25432/novel_db
DEV_REDIS_URL ?= redis://localhost:26379/0
DEV_CELERY_BROKER_URL ?= redis://localhost:26379/1
DEV_ENV := DATABASE_URL="$(DEV_DATABASE_URL)" REDIS_URL="$(DEV_REDIS_URL)" CELERY_BROKER_URL="$(DEV_CELERY_BROKER_URL)"

.PHONY: help install web-install infra migrate migrate-safe dev-api dev-worker dev-beat dev-web dev stop test test-offline offline-report lint dev-reset logs

help:
	@echo "Available targets:"
	@echo "  make install      - install Python deps with uv and web deps with npm"
	@echo "  make dev          - one-command full dev startup (db+redis+api+worker+web)"
	@echo "  make dev-reset    - reset infra and recreate local state"
	@echo "  make logs         - tail docker compose logs"
	@echo "  make stop         - stop docker infra"
	@echo "  make test         - run backend tests with uv"

install:
	@command -v uv >/dev/null || (echo "uv is required. Install from https://docs.astral.sh/uv/" && exit 1)
	@uv sync --extra dev
	@$(MAKE) web-install

web-install:
	@cd web && npm install

infra:
	@docker compose up -d --wait --wait-timeout 60

migrate:
	@$(DEV_ENV) uv run alembic upgrade head

migrate-safe:
	@$(DEV_ENV) PGOPTIONS='-c lock_timeout=5s -c statement_timeout=120s' uv run alembic upgrade head

dev-api:
	@$(DEV_ENV) uv run uvicorn app.main:app --reload --host 127.0.0.1

dev-worker:
	@$(DEV_ENV) uv run celery -A app.workers.celery_app worker -l info

dev-beat:
	@$(DEV_ENV) uv run celery -A app.workers.celery_app beat -l info

dev-web:
	@cd web && npm run dev -- --hostname 127.0.0.1

dev:
	@set -euo pipefail; \
	command -v uv >/dev/null || (echo "uv is required. Install from https://docs.astral.sh/uv/" && exit 1); \
	command -v npm >/dev/null || (echo "npm is required." && exit 1); \
	export DATABASE_URL="$(DEV_DATABASE_URL)"; \
	export REDIS_URL="$(DEV_REDIS_URL)"; \
	export CELERY_BROKER_URL="$(DEV_CELERY_BROKER_URL)"; \
	docker compose up -d --wait --wait-timeout 60; \
	uv sync --extra dev; \
	cd web && npm install; \
	cd ..; \
	uv run alembic upgrade head; \
	start_pgroup() { \
		if command -v setsid >/dev/null 2>&1; then \
			setsid "$$@" & \
		else \
			python3 -c 'import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' "$$@" & \
		fi; \
		last_bg_pid=$$!; \
	}; \
	cleanup() { \
		code=$$?; \
		trap - INT TERM EXIT; \
		for p in "$${api_pid:-}" "$${worker_pid:-}" "$${beat_pid:-}" "$${web_pid:-}"; do \
			[ -n "$$p" ] || continue; \
			kill -TERM -- "-$$p" 2>/dev/null || true; \
		done; \
		sleep 0.5; \
		for p in "$${api_pid:-}" "$${worker_pid:-}" "$${beat_pid:-}" "$${web_pid:-}"; do \
			[ -n "$$p" ] || continue; \
			kill -KILL -- "-$$p" 2>/dev/null || true; \
		done; \
		exit $$code; \
	}; \
	trap cleanup INT TERM EXIT; \
	start_pgroup uv run uvicorn app.main:app --reload --host 127.0.0.1; api_pid=$$last_bg_pid; \
	start_pgroup uv run celery -A app.workers.celery_app worker -l info; worker_pid=$$last_bg_pid; \
	start_pgroup uv run celery -A app.workers.celery_app beat -l info; beat_pid=$$last_bg_pid; \
	start_pgroup bash -lc 'cd web && npm run dev -- --hostname 127.0.0.1'; web_pid=$$last_bg_pid; \
	wait

stop:
	@docker compose down

dev-reset:
	@docker compose down -v
	@docker compose up -d --build --wait --wait-timeout 60
	@$(DEV_ENV) uv run alembic upgrade head

logs:
	@docker compose logs -f --tail=200

test:
	@uv run pytest -q

test-offline:
	@uv run pytest -q -m offline

offline-report:
	@uv run python scripts/offline_harness_report.py --enforce-baseline

lint:
	@uv run python -m compileall app alembic
