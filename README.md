# AI-JinShu: AI Novel Generation Platform

AI-JinShu is an **AI novel generation** platform for **long-form novel** workflows, built with **LangGraph**, **FastAPI**, **Next.js**, **Celery**, and **Docker**. It focuses on long-running chapter generation, structured review, retry/resume orchestration, and deployable monorepo workflows.

## What It Does

- 长篇小说分卷生成与章节级写作
- LangGraph 驱动的 `init -> prewrite -> outline -> writer -> review -> finalize` 主链
- 多路结构化审校、质量报告与事实抽取
- Celery 任务调度、暂停、恢复与重试
- 单镜像 Docker 打包，部署时由单应用容器承载前后端与后台任务

## Quick Start

### Local Development

```bash
cp .env.example .env
cp web/.env.example web/.env
make dev
```

- Web: `http://127.0.0.1:3000`
- API: `http://127.0.0.1:8000`
- Health: `http://127.0.0.1:8000/health`

### Quick Deploy

```bash
cp .env.deploy.example .env.deploy
docker compose -f docker-compose.deploy.yml --env-file .env.deploy up -d
```

默认会拉取 GitHub Actions 发布的镜像：`ghcr.io/aapw01/ai-jinshu:latest`

如果先本地构建镜像：

```bash
docker build -t ai-jinshu .
APP_IMAGE=ai-jinshu docker compose -f docker-compose.deploy.yml --env-file .env.deploy up -d
```

## Architecture

| Layer | Stack | Purpose |
|---|---|---|
| API | FastAPI | REST API, auth, admin settings, generation control |
| Worker | Celery + Redis | generation / rewrite / storyboard background jobs |
| Data | PostgreSQL + pgvector | novels, versions, checkpoints, memory, embeddings |
| Web | Next.js | dashboard, novel editor, task views |
| Orchestration | LangGraph | chapter pipeline, review gates, resume/retry flow |

## LangGraph Flow

- `init`: 初始化任务、恢复 runtime state、加载版本上下文
- `prewrite`: 生成宪法、规格、剧情蓝图等预写作资产
- `outline`: 生成或补齐分卷章节大纲
- `writer`: 根据大纲和上下文生成正文
- `review`: 做结构、事实、推进、美学多路审校
- `finalize`: 定稿、长度收口、facts/progression 抽取
- `closure / volume / final_review`: 处理分卷衔接、尾部重写与整书终审

特点：
- 支持 retry / resume
- 支持长篇分卷
- 支持质量门控与阶段化恢复

## Environment

- 开发环境：见 [`.env.example`](.env.example)
- Web 本地变量：见 [`web/.env.example`](web/.env.example)
- 部署环境：见 [`.env.deploy.example`](.env.deploy.example)

## Deployment

- 单镜像 Dockerfile：[`Dockerfile`](Dockerfile)
- 生产 compose：[`docker-compose.deploy.yml`](docker-compose.deploy.yml)
- 详细部署说明：[`docs/deployment.md`](docs/deployment.md)
- GitHub Actions 会构建并发布多架构 GHCR 镜像
- 部署形态为：`postgres`、`redis` 两个基础容器 + 一个 `app` 应用容器（内含 `api/web/worker/beat`）

## Common Commands

| Command | Purpose |
|---|---|
| `make install` | 安装后端与前端依赖 |
| `make dev` | 启动 postgres、redis、api、worker、beat、web |
| `make stop` | 停止本地基础设施 |
| `make test` | 运行后端测试 |
| `make lint` | 编译检查后端 Python 模块 |

## Docs

- [API 文档](docs/api.md)
- [任务调度说明](docs/task-scheduling.md)
- [数据库说明](docs/database.md)
- [部署说明](docs/deployment.md)
