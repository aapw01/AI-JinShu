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

## Auth Config Notes

- `AUTH_REQUIRE_EMAIL_VERIFICATION=false`（默认）：注册后可直接登录。
- `AUTH_REQUIRE_EMAIL_VERIFICATION=true`：注册后需邮件激活；必须同时配置 `SENDGRID_API_KEY` 和 `SENDGRID_FROM_EMAIL`。

## Quota Config Notes

- 优先级规则：**系统设置页面（DB） > 环境变量（.env）**；未在页面配置的项自动回退到环境变量。
- `QUOTA_ENFORCE_CONCURRENCY_LIMIT=false`（默认）：不限制并发创建任务。
- `QUOTA_ENFORCE_CONCURRENCY_LIMIT=true`：按套餐的 `max_concurrent_tasks` 启用并发上限。
- `CREATION_SCHEDULER_ENABLED=true`：启用统一创作任务调度（Generation + Rewrite + Storyboard）。
- `CREATION_DEFAULT_MAX_CONCURRENT_TASKS=1`：每用户默认并发运行任务数。
- `CREATION_DISPATCH_POLL_SECONDS=2`：调度轮询间隔（秒）。
- `CREATION_MAX_DISPATCH_BATCH=5`：单次调度最多派发任务数。
- `QUOTA_FREE_MONTHLY_CHAPTER_LIMIT`：普通用户月度章节限额（默认 `1000000`）。
- `QUOTA_FREE_MONTHLY_TOKEN_LIMIT`：普通用户月度 token 限额（默认 `10000000000`）。
- `QUOTA_ADMIN_MONTHLY_CHAPTER_LIMIT`：管理员月度章节限额（默认 `10000000`）。
- `QUOTA_ADMIN_MONTHLY_TOKEN_LIMIT`：管理员月度 token 限额（默认 `100000000000`）。
- `SYSTEM_SETTINGS_MASTER_KEY`：可选；配置后管理员在系统设置页保存的 API Key 将加密存储（未配置则明文存储并有风险提示）。

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
- Auth: `POST /api/auth/register|login|logout`, `GET /api/auth/me`, `POST /api/auth/verify-email/*`, `POST /api/auth/password/*`
- Generation control: `POST /api/novels/{id}/generation/pause|resume|cancel`, `GET /api/novels/{id}/generation/tasks`
- Account quota/billing: `GET /api/account/quota`, `GET /api/account/ledger`
- Notifications: `GET /api/account/notifications`
- Rewrite diff: `GET /api/novels/{id}/versions/{version_id}/diff?compare_to={base_version_id}`
- Admin observability: `GET /api/admin/observability/summary`
- Admin system settings: `GET/PUT /api/admin/settings/models`, `GET/PUT /api/admin/settings/runtime`, `GET /api/admin/settings/effective`
- Storyboard: `POST /api/storyboards`, `POST /api/storyboards/{id}/generate`, `GET /api/storyboards/{id}/status`
- Storyboard styles: `GET /api/storyboards/style-presets`, `POST /api/storyboards/style-recommendations`
- Character profiles: `GET /api/novels/{id}/character-profiles`
- Storyboard workbench: `GET /api/storyboards/{id}/versions`, `GET/PUT /api/storyboards/{id}/shots`, `GET /api/storyboards/{id}/characters`, `POST /api/storyboards/{id}/characters/generate`, `POST /api/storyboards/{id}/versions/{version_id}/optimize|finalize`, `GET /api/storyboards/{id}/export/csv`, `GET /api/storyboards/{id}/characters/export`

## Docs

- Longform technical plan: `docs/longform-novel-technical-plan.md`
- API details: `docs/api.md`
- Database notes: `docs/database.md`
- Task scheduling system: `docs/task-scheduling.md`

## Logging & Trace

- Structured JSON logs are enabled by default.
- Every API response includes `X-Trace-Id`.
- Log envs: `LOG_FORMAT`, `LOG_LEVEL`, `LOG_SLOW_THRESHOLD_MS`, `LOG_NODE_SLOW_THRESHOLD_MS`, `LOG_REDACTION_LEVEL`.

## Token Usage

- 任务统计返回 `token_usage_input/token_usage_output/estimated_cost`。
- 统计口径为“全任务全阶段累计”：优先使用模型返回的真实 `usage`，缺失时回退估算。
- 该口径对 generation/rewrite/storyboard 一致生效，便于前端展示与后续计费对账。
