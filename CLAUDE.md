# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development commands

- Install dependencies (backend + frontend):
  - `make install` / `just install`
- Full local development stack (Postgres + Redis + migrations + API + Celery worker + Celery beat + Next.js):
  - `make dev` / `just dev`
- Stop local infra: `make stop`
- Reset infra/data: `make dev-reset`
- Tail Docker logs: `make logs`
- Backend tests: `make test` / `uv run pytest -q`
- Run a single test file: `uv run pytest -q tests/test_storyboard_api.py`
- Run tests matching a keyword: `uv run pytest -q -k "generation"`
- Backend lint check: `make lint`
- Run services separately when debugging:
  - API: `make dev-api` | Celery worker: `make dev-worker` | Beat: `make dev-beat` | Frontend: `make dev-web`
- DB migrations: `make migrate`

## Architecture overview

### Monorepo shape
- `app/`: FastAPI backend + domain services + Celery tasks.
- `web/`: Next.js frontend and API client.
- `alembic/`: database migrations.
- `tests/`: backend tests.
- `presets/`: YAML configuration for strategies, genres, languages, lengths, etc.

### Backend layer map
```
app/core/          — config, auth (JWT), database, LLM factory, tracing, i18n
app/models/        — SQLAlchemy ORM (novel, creation_task, storyboard)
app/schemas/       — Pydantic request/response shapes
app/api/routes/    — FastAPI routers (novels, chapters, generation, rewrite, storyboard, auth, admin)
app/services/      — domain logic (generation, rewrite, storyboard, scheduler, memory, quota, system_settings)
app/tasks/         — Celery task entrypoints (generation.py, rewrite.py, storyboard.py, scheduler.py)
app/workers/       — Celery app definition (celery_app.py)
app/prompts/       — Jinja2 template loader; templates live in app/prompts/templates/*.j2
```

### Request and task flow
1. `app/main.py` registers all routers. API routes enqueue work via `creation_tasks` rather than running inline.
2. Scheduler tick in `app/services/scheduler/scheduler_service.py` dispatches queued tasks respecting per-user concurrency limits.
3. State machine: `queued → dispatching → running → completed | failed | cancelled` (+ `paused` / resume).
4. Celery workers execute concrete work in `app/tasks/generation.py`, `app/tasks/rewrite.py`, `app/tasks/storyboard.py`.

### Generation pipeline (core business path)
- Public entry: `run_generation_pipeline_langgraph` in `app/services/generation/graph.py` (re-exported via `langgraph_pipeline.py`).
- The LangGraph graph is compiled **once as a module-level singleton** — avoid re-importing in hot paths.
- Node files under `app/services/generation/nodes/`: `init_node`, `writer`, `review`, `finalize`, `final_review`, `closure`, `volume`, `chapter_loop`.
- `GenerationState` TypedDict is defined in `app/services/generation/state.py` — the single source of truth for what flows between nodes.
- Chapter text extraction uses structured output via `app/core/llm_contract.py`; it tries `function_calling → json_schema → json_mode` in adapter-dependent order.
- Scoring, review-gate logic, and normalization helpers live in `app/services/generation/heuristics.py`.
- Closure/bridge chapter decisions are policy objects in `app/services/generation/policies.py` (pure functions, no side effects).
- Chapter length policies and feedback are in `app/services/generation/length_control.py`.

### LLM abstraction
- `app/core/llm.py`: unified `get_llm()` factory supporting `openai_compatible`, `gemini`, and `anthropic` adapter types.
- Runtime LLM config (model, API key, base URL) is resolved from **DB first, then env** via `app/services/system_settings/runtime.py`, with a 5-second TTL cache.
- Call `invalidate_caches()` in system settings runtime after any admin model config change.
- `app/core/llm_usage.py`: token usage is recorded per call and accumulated in task result payloads.

### Strategy and prompt system
- Strategies are YAML presets in `presets/strategies/*.yaml` mapping pipeline stages (`outliner`, `writer`, `reviewer`, etc.) to provider/model overrides.
- `app/core/strategy.py` merges a strategy YAML over the default stage map; `__default__` values fall back to the primary runtime model.
- Prompts use Jinja2 (`.j2`) under `app/prompts/templates/`; rendered via `app/prompts.render_prompt(template_name, **kwargs)`.

### Memory subsystem
`app/services/memory/` stores per-novel persistent state used across chapters:
- `story_bible.py` — canonical world/character facts, quality reports, checkpoints.
- `character_state.py` — per-character state snapshots.
- `progression_state.py` — plot progression and thread tracking.
- `summary_manager.py` — rolling chapter summaries.
- `vector_store.py` — pgvector-backed semantic search (falls back to `Text` column in non-Postgres environments).

### Task runtime (checkpoint/resume)
`app/services/task_runtime/`:
- `checkpoint_repo.py` — persists completed chapter checkpoints so interrupted jobs can skip already-done work.
- `cursor_service.py` — tracks resume position within a generation run.
- `lease_service.py` — worker heartbeat/lease mechanism to detect stale workers.

### Rewrite and storyboard
- Both share the same `creation_tasks` scheduler and concurrency controls as generation.
- Storyboard: `app/services/storyboard/` + `app/api/routes/storyboards.py`.
- Rewrite: `app/services/rewrite/` + `app/api/routes/rewrite.py`.

### Data and infra
- PostgreSQL (local: port `25432`): source of truth. Novel model uses `pgvector.sqlalchemy.Vector(1536)` for embeddings; falls back to `Text` when no Postgres URL.
- Redis (local: port `26379`): Celery broker/state backend + realtime task progress cache.

### Frontend integration
- `web/lib/api.ts`: central typed API client — change this first when modifying backend response shapes.
- `web/app/**`: Next.js App Router pages consuming the API client.

## Operational constraints

- `X-Trace-Id` header is injected by middleware in `app/main.py` — preserve this when modifying request flow.
- Token usage accounting spans generation/rewrite/storyboard and surfaces in task result payloads — keep consistent when changing task result schemas.
- Concurrency, pause/resume/cancel are scheduler-driven (`scheduler_service.py`, `task_runtime/`), not route-local — don't implement task control only in route handlers.
- The LangGraph graph singleton is built at import time; graph structure changes require process restart.
