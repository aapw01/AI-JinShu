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
	trap 'kill 0' INT TERM EXIT; \
	uv run uvicorn app.main:app --reload & \
	uv run celery -A app.workers.celery_app worker -l info & \
	(cd web && npm run dev) & \
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
