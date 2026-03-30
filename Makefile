SHELL := /bin/bash

.PHONY: help install web-install infra migrate migrate-safe dev-api dev-worker dev-beat dev-web dev stop test lint dev-reset logs

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
	@docker compose up -d

migrate:
	@uv run alembic upgrade head

migrate-safe:
	@PGOPTIONS='-c lock_timeout=5s -c statement_timeout=120s' uv run alembic upgrade head

dev-api:
	@uv run uvicorn app.main:app --reload --host 127.0.0.1

dev-worker:
	@uv run celery -A app.workers.celery_app worker -l info

dev-beat:
	@uv run celery -A app.workers.celery_app beat -l info

dev-web:
	@cd web && npm run dev -- --hostname 127.0.0.1

dev:
	@set -euo pipefail; \
	command -v uv >/dev/null || (echo "uv is required. Install from https://docs.astral.sh/uv/" && exit 1); \
	command -v npm >/dev/null || (echo "npm is required." && exit 1); \
	docker compose up -d; \
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
	@docker compose up -d --build
	@uv run alembic upgrade head

logs:
	@docker compose logs -f --tail=200

test:
	@uv run pytest -q

lint:
	@uv run python -m compileall app alembic
