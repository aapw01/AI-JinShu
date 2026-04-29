# Deployment

## Quick Deploy With Published Image

1. 复制部署环境变量：

```bash
cp .env.deploy.example .env.deploy
```

2. 修改 `.env.deploy` 中至少这些字段：
- `APP_IMAGE`
- `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `LLM_API_KEY`
- `AUTH_JWT_SECRET`
- `AUTH_FRONTEND_BASE_URL`

3. 启动服务：

```bash
docker compose -f docker-compose.deploy.yml --env-file .env.deploy up -d
```

`app` 容器启动时会默认执行 `alembic upgrade head`，迁移成功后再启动 API、Web、worker 和 beat。需要临时关闭自动迁移时，可在 `.env.deploy` 中设置 `AUTO_MIGRATE_ON_START=false`。

默认发布镜像地址：

```bash
ghcr.io/aapw01/ai-jinshu:latest
```

4. 健康检查：

```bash
curl http://localhost:8000/health
```

## Build Local Image First

如果暂时不想用 GHCR，可先在本地构建：

```bash
docker build -t ai-jinshu .
APP_IMAGE=ai-jinshu docker compose -f docker-compose.deploy.yml --env-file .env.deploy up -d
```

## Services

- `app`: 单应用容器，内部用 `supervisord` 同时管理：
  - FastAPI API
  - Next.js production server
  - Celery worker
  - Celery beat
  - 启动前默认执行 Alembic 数据库迁移
- `postgres`: PostgreSQL + pgvector
- `redis`: Redis broker/cache
