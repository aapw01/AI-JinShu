# AI-JinShu - AI Novel Generation Platform

Monorepo for AI-powered novel generation with LangGraph pipeline.

## Structure

```
AI-JinShu/
├── app/              # FastAPI Python backend
├── web/              # Next.js TypeScript frontend
├── alembic/          # Database migrations
├── presets/          # Strategy/config presets
├── tests/            # Backend tests
├── docker-compose.yml
└── .env.example
```

## Quick Start

1. Install `uv` (Python env/deps manager): <https://docs.astral.sh/uv/>
2. Copy `.env.example` to `.env` and configure API keys
3. One-command startup (all services): `make dev`
4. Or use just: `just dev`

## Dev Commands

- `make install` / `just install`: install Python deps via `uv` and frontend deps via `npm`
- `make dev` / `just dev`: start postgres+redis, run migrations, and launch API+worker+web
- `make stop` / `just stop`: stop docker infra
- `make test` / `just test`: run backend tests
- `make lint` / `just lint`: compile-check backend Python modules

## API

- Health: `GET /health`
- Novels CRUD: `GET/POST /api/novels`, `GET/PUT/DELETE /api/novels/{id}`
- Generation: `POST /api/novels/{id}/generate`, `GET /api/novels/{id}/generation/status`
- Longform reports: `GET /api/novels/{id}/quality-reports`, `GET /api/novels/{id}/checkpoints`, `GET /api/novels/{id}/volumes/summary`, `GET /api/novels/{id}/volumes/{volume_no}/gate-report`
- Feedback loop: `GET/POST /api/novels/{id}/feedback`
- Observability: `GET /api/novels/{id}/observability`
- Chapters: `GET /api/novels/{id}/chapters`, `GET /api/novels/{id}/chapters/{num}`
- Export: `GET /api/novels/{id}/export?format=txt|md|zip`
- Presets: `GET /api/presets`

## Docs

- Longform technical plan: `docs/longform-novel-technical-plan.md`
- API details: `docs/api.md`
- Database notes: `docs/database.md`
