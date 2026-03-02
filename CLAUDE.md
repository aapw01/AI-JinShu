# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development commands

- Install dependencies (backend + frontend):
  - `make install`
  - or `just install`
- Full local development stack (Postgres + Redis + migrations + API + Celery worker + Celery beat + Next.js):
  - `make dev`
  - or `just dev`
- Stop local infra:
  - `make stop`
  - or `just stop`
- Reset infra/data and rebuild local state:
  - `make dev-reset`
- Tail Docker logs:
  - `make logs`
- Backend tests:
  - `make test`
  - or `uv run pytest -q`
- Run a single test file:
  - `uv run pytest -q tests/test_storyboard_api.py`
- Run tests matching a keyword:
  - `uv run pytest -q -k "generation"`
- Backend compile/lint check:
  - `make lint`
- Run services separately when debugging:
  - API: `make dev-api`
  - Celery worker: `make dev-worker`
  - Celery beat: `make dev-beat`
  - Frontend: `make dev-web`
- DB migrations:
  - `make migrate`

## Architecture overview

### Monorepo shape
- `app/`: FastAPI backend + domain services + Celery tasks.
- `web/`: Next.js frontend and API client.
- `alembic/`: database migrations.
- `tests/`: backend tests (API, scheduler/task runtime, generation policies, storyboard, auth/security).

### Backend request and task flow
1. `app/main.py` registers all API routers (novels, chapters, generation, rewrite, auth/account, storyboard, admin).
2. Most long-running operations are async workloads; API routes enqueue work rather than doing full generation inline.
3. Unified scheduling is centered in `app/services/scheduler/scheduler_service.py` with a shared `creation_tasks` model/state machine:
   - statuses: `queued -> dispatching -> running -> completed|failed|cancelled` (+ `paused`, resume paths).
4. Celery app is in `app/workers/celery_app.py` and includes generation/rewrite/storyboard/scheduler tasks.
5. Task dispatch and execution:
   - scheduler tick dispatches queued tasks by user concurrency limits.
   - concrete workers run in `app/tasks/generation.py`, `app/tasks/rewrite.py`, `app/tasks/storyboard.py`.

### Generation system (core business path)
- Generation orchestration uses LangGraph in `app/services/generation/langgraph_pipeline.py`.
- Worker entrypoint (`submit_generation_task` alias) is in `app/tasks/generation.py`.
- Book-level generation is split into volume chunks; progress/status are persisted to both:
  - Redis (realtime status cache), and
  - PostgreSQL (`generation_tasks` + unified `creation_tasks`).
- Checkpoint/resume for interrupted jobs is handled via `app/services/task_runtime/*`.

### Rewrite and storyboard integration
- Rewrite and storyboard pipelines are first-class creation task types and share scheduler/concurrency controls.
- Storyboard domain logic lives under `app/services/storyboard/` with dedicated API routes in `app/api/routes/storyboards.py`.
- Rewrite pipeline logic lives under `app/services/rewrite/` with routes in `app/api/routes/rewrite.py`.

### Data and infra responsibilities
- PostgreSQL: source of truth (novels/chapters/versions/tasks/quota ledgers, etc.).
- Redis:
  - Celery broker/state backend,
  - realtime task progress cache used by status endpoints.
- Docker compose provides local Postgres (`25432`) and Redis (`26379`).

### Frontend integration pattern
- `web/lib/api.ts` is the central typed API client for backend endpoints.
- It standardizes auth token handling, error normalization, and all novel/generation/rewrite/storyboard calls.
- Next.js pages in `web/app/**` consume this client; backend API shape changes should be reflected in `web/lib/api.ts` first.

## Operational notes relevant for coding

- API responses include `X-Trace-Id` from middleware in `app/main.py`; preserve this behavior when touching request flow.
- Token usage accounting is aggregated across generation/rewrite/storyboard and surfaced in task/status payloads; keep this consistent when modifying task result schemas.
- Concurrency and pause/resume/cancel behavior are scheduler-driven, not route-local; changes to task control should usually be made in scheduler/task runtime services, not only in route handlers.
