SHELL := /bin/bash

.PHONY: help install web-install infra migrate dev-api dev-worker dev-web dev stop test lint dev-reset logs

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

dev-api:
	@uv run uvicorn app.main:app --reload

dev-worker:
	@uv run celery -A app.workers.celery_app worker -l info

dev-web:
	@cd web && npm run dev

dev:
	@set -euo pipefail; \
	command -v uv >/dev/null || (echo "uv is required. Install from https://docs.astral.sh/uv/" && exit 1); \
	command -v npm >/dev/null || (echo "npm is required." && exit 1); \
	docker compose up -d; \
	uv sync --extra dev; \
	cd web && npm install; \
	cd ..; \
	uv run alembic upgrade head; \
	kill_tree() { \
		pid="$$1"; \
		children="$$(pgrep -P "$$pid" || true)"; \
		for c in $$children; do kill_tree "$$c"; done; \
		kill "$$pid" 2>/dev/null || true; \
	}; \
	cleanup() { \
		code=$$?; \
		trap - INT TERM EXIT; \
		for p in "$${api_pid:-}" "$${worker_pid:-}" "$${web_pid:-}"; do \
			[ -n "$$p" ] || continue; \
			kill_tree "$$p"; \
		done; \
		sleep 0.5; \
		for p in "$${api_pid:-}" "$${worker_pid:-}" "$${web_pid:-}"; do \
			[ -n "$$p" ] || continue; \
			kill -9 "$$p" 2>/dev/null || true; \
		done; \
		exit $$code; \
	}; \
	trap cleanup INT TERM EXIT; \
	uv run uvicorn app.main:app --reload & api_pid=$$!; \
	uv run celery -A app.workers.celery_app worker -l info & worker_pid=$$!; \
	(cd web && npm run dev) & web_pid=$$!; \
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
