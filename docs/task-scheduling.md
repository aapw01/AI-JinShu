# Task Scheduling System

本文档说明当前 AI-JinShu 的统一创作任务调度实现（异步任务，不含业务周期任务）。

## 1. 总览

- 调度框架：Celery
- Broker：Redis DB 1（`CELERY_BROKER_URL`）
- 状态缓存：Redis DB 0（`REDIS_URL`）
- 持久化状态：PostgreSQL（统一 `creation_tasks` + 业务表）
- Worker 启动命令：`uv run celery -A app.workers.celery_app worker -l info`

创作任务（Generation + Rewrite + Storyboard）使用统一状态机：
- `queued -> dispatching -> running -> completed|failed|cancelled`
- `running -> paused`
- `paused -> queued`

## 2. 调度架构

```text
Client -> FastAPI Route -> Celery delay() -> Redis Broker -> Celery Worker
              |                                  |
              |                                  +-> 执行任务函数（generation/storyboard/rewrite）
              |
              +-> PostgreSQL 记录统一任务行（creation_tasks）
              +-> Redis 状态缓存（实时进度）
```

关键实现位置：
- Celery 应用配置：`app/workers/celery_app.py`
- 统一调度服务：`app/services/scheduler/scheduler_service.py`
- 并发控制：`app/services/scheduler/concurrency_service.py`
- 调度 tick：`app/tasks/scheduler.py`
- 生成执行器：`app/tasks/generation.py`
- 重写执行器：`app/tasks/rewrite.py`
- 生成任务 API：`app/api/routes/generation.py`
- 分镜任务 API：`app/api/routes/storyboards.py`
- 重写任务 API：`app/api/routes/rewrite.py`

## 3. Celery 配置要点

`app/workers/celery_app.py` 中的关键配置：

- `task_acks_late=True`：任务完成后再 ack，提升异常中断后的可恢复性
- `task_reject_on_worker_lost=True`：worker 丢失时拒绝任务，触发重投
- `worker_prefetch_multiplier=1`：减少长任务饥饿，提升公平性
- `task_track_started=True`：任务进入 started 阶段可追踪
- `broker_connection_retry_on_startup=True`：启动时自动重试 broker 连接

系统设置优先级：
- 调度/配额的运行时参数优先读取管理员后台系统设置（DB），无配置时回退 `.env`。
- 目前仍固定走 `.env` 的轮询参数：`CREATION_DISPATCH_POLL_SECONDS`、`CREATION_RECOVERY_POLL_SECONDS`（不支持后台热更新）。

## 4. 创作任务调度流（Generation + Rewrite + Storyboard）

## 4.1 小说生成（Generation）

入口 API（generation）：
- `POST /api/novels/{novel_id}/generate`
- `POST /api/novels/{novel_id}/generation/retry`

任务函数：
- `submit_book_generation_task`（别名：`submit_generation_task`）
- `submit_volume_generation_task`（可独立调用，但当前主流程由 book orchestrator 串行执行）

核心机制：
- API 只负责创建 `creation_tasks`（状态 `queued`）
- 调度器按用户并发额度抢占：`queued -> dispatching -> running`
- 默认并发额度来自系统设置 `creation_default_max_concurrent_tasks`，无覆盖时回退 `CREATION_DEFAULT_MAX_CONCURRENT_TASKS`（默认 1）
- 用户级额度优先取 `user_quotas.max_concurrent_tasks`
- `paused` 不占用并发槽位
- worker 终态后自动触发下一任务派发

控制接口（旧接口仍可用）：
- `POST /generation/pause`
- `POST /generation/resume`
- `POST /generation/cancel` 或 `DELETE /generation/{task_id}`
- `GET /generation/tasks`
- `GET /generation/status`

## 4.2 导演分镜（Storyboard）

入口 API（V2）：
- `POST /api/storyboards/{project_id}/preflight`
- `POST /api/storyboards/{project_id}/runs`
- `POST /api/storyboards/{project_id}/runs/{run_id}/actions`

任务函数：
- `run_storyboard_lane`（按 lane 分拆子任务）
- `run_storyboard_export`（异步导出）

核心机制：
- 预检查（preflight）先落 `storyboard_source_snapshots` 和 `storyboard_gate_reports`
- `storyboard_runs` 代表一次运行，`storyboard_run_lanes` 代表 lane 子状态
- 每个 lane 子任务同时写统一调度表 `creation_tasks`（`task_type=storyboard_lane`）
- 运行事件写入 `storyboard_events_outbox`，并发布 Redis 频道 `storyboard:events`
- 导出走 `storyboard_exports` 异步任务，产物落盘到 `tmp/storyboard_exports/`

状态查询：
- `GET /api/storyboards/{project_id}/runs`
- `GET /api/storyboards/{project_id}/runs/{run_id}`
- 兼容保留：`GET /api/storyboards/{project_id}/status`

控制接口：
- `POST /api/storyboards/{project_id}/runs/{run_id}/actions`（`pause|resume|cancel|retry`）
- 兼容保留：`POST /api/storyboards/{project_id}/pause|resume|cancel|retry`

## 4.3 章节重写（Rewrite）

入口 API：
- `POST /api/novels/{novel_id}/rewrite-requests`
- `POST /api/novels/{novel_id}/rewrite-requests/{request_id}/retry`

任务函数：
- `submit_rewrite_task`

核心机制：
- Rewrite 任务同样进入 `creation_tasks` 队列
- `rewrite_requests` 保留业务语义字段（章节范围/注释/结果）
- 调度与并发控制统一由 scheduler 负责

## 5. 任务控制接口（按业务域）

- Generation：`POST /api/novels/{novel_id}/generation/pause|resume|cancel`
- Generation 任务列表：`GET /api/novels/{novel_id}/generation/tasks`
- Rewrite/Storyboard 通过各自业务接口触发、暂停、恢复与取消。
- 任务详情包含 `token_usage_input/token_usage_output/estimated_cost`，用于展示任务消耗。

## 6. 状态模型

常见 `status`：
- `queued`
- `dispatching`
- `running`
- `paused`
- `cancelled`
- `completed`
- `failed`

设计原则：
- Redis 提供实时性（低延迟）
- PostgreSQL 提供权威持久化（可审计、可回溯、可回退）

## 6.1 Token 统计口径

- 统一由 `app/core/llm.py` 的代理层采集 usage，避免在业务节点耦合解析逻辑。
- 任务维度用 `app/core/llm_usage.py` 聚合全阶段 token（含分阶段 `stages` 统计）。
- 优先使用模型真实 usage（`usage_metadata` / `response_metadata.token_usage`），缺失时回退估算。
- generation/rewrite/storyboard 的累计结果统一回填到 `creation_tasks.result_json`。

## 7. 运行与运维

本地依赖：
- PostgreSQL（`docker-compose.yml`）
- Redis（`docker-compose.yml`）
- API + Worker 并行运行（`make dev`）

常用命令：
- 启基础设施：`make infra`
- 启 API：`make dev-api`
- 启 Worker：`make dev-worker`
- 一键全启动：`make dev`

## 8. 排障建议

- 任务长期 `submitted`：
  - 检查 worker 进程是否启动
  - 检查 `CELERY_BROKER_URL` 是否可达
- 状态查不到：
  - 先看 Redis key 是否存在
  - 再看 DB 对应任务行是否更新
- 任务被卡住：
  - 对 generation 检查是否处于 `paused`
  - 查看任务 `error_code/error_category/retryable` 决定是重试还是人工处理
- 任务重复提交冲突（409）：
  - 系统默认限制同一资源同一时刻只跑一个 active task
