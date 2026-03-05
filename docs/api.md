# API 文档补充（长篇生成能力）

## Longform Endpoints

## Auth Endpoints

### `POST /api/auth/register`
- 入参：`email`, `password`
- 行为：创建用户；若开启邮箱激活则发送激活邮件并返回提示，否则直接登录返回 token。
- 配置：
  - `AUTH_REQUIRE_EMAIL_VERIFICATION=false`（默认）：直接登录。
  - `AUTH_REQUIRE_EMAIL_VERIFICATION=true`：必须配置 `SENDGRID_API_KEY` 与 `SENDGRID_FROM_EMAIL`，否则返回 `503`。

### `POST /api/auth/login`
- 入参：`email`, `password`
- 返回：`access_token` + `user`。
- 风控：失败次数过多会临时锁定账号。

### `POST /api/auth/logout`
- 行为：清除登录 cookie。

### `GET /api/auth/me`
- 行为：返回当前登录用户信息。

### `POST /api/auth/verify-email/request`
- 入参：`email`
- 行为：发送激活邮件（若账户存在且未激活）。

### `POST /api/auth/verify-email/confirm`
- 入参：`token`
- 行为：完成邮箱激活。

### `POST /api/auth/password/forgot`
- 入参：`email`
- 行为：发送重置密码邮件（若账户存在）。

### `POST /api/auth/password/reset`
- 入参：`token`, `new_password`
- 行为：重置密码并失效本次 token。

## 日志与追踪约定

- 所有 API 响应头返回：`X-Trace-Id`。
- 生产默认日志：`JSON + INFO`。
- 关键日志事件：
  - `api.request.received|completed|failed|slow`
  - `auth.login.success|failed`、`authz.denied`
  - `generation.submit|retry.submit|pause|resume|cancel|status.fallback_db|task.finalized|quota.blocked`
  - `pipeline.node.start|end|error|slow`
  - `rewrite.request.created|retry`、`rewrite.chapter.start|end|llm_error`、`rewrite.completed|failed`
  - `llm.call.start|success|error`、`embed.query.success|fallback`
  - `mail.send.success|error|skipped`
- 日志配置项：
  - `LOG_FORMAT`
  - `LOG_LEVEL`
  - `LOG_SLOW_THRESHOLD_MS`
  - `LOG_NODE_SLOW_THRESHOLD_MS`
  - `LOG_REDACTION_LEVEL`

### `GET /api/novels/{novel_id}/quality-reports`
- 作用：查询章节/卷/全书质量报告。
- 参数：`scope`（可选，`chapter|volume|book`），`scope_id`（可选），`limit`（默认 200）。
- 返回：报告列表（`scope`, `scope_id`, `verdict`, `metrics`, `created_at`）。

### `GET /api/novels/{novel_id}/checkpoints`
- 作用：查询可恢复的生成检查点。
- 参数：`task_id`（可选），`limit`（默认 200）。
- 返回：检查点列表（`task_id`, `volume_no`, `chapter_num`, `node`, `state`）。

### `GET /api/novels/{novel_id}/volumes/summary`
- 作用：按卷查看生成进度摘要。
- 参数：`volume_size`（默认 30）。
- 返回：卷列表（`volume_no`, `start_chapter`, `end_chapter`, `completed_chapters`, `target_chapters`, `snapshot_id`, `quality_verdict`）。

### `GET /api/novels/{novel_id}/volumes/{volume_no}/gate-report`
- 作用：查询指定卷的门禁报告（质量判定 + 证据链 + checkpoint 状态）。
- 返回：`verdict`, `metrics`, `evidence_chain`, `checkpoint_state`。

### `GET /api/novels/{novel_id}/feedback`
- 作用：查询人工反馈记录（编辑/读者）。
- 参数：`limit`（默认 200）。
- 返回：反馈列表（`chapter_num`, `volume_no`, `feedback_type`, `rating`, `tags`, `comment`）。

### `POST /api/novels/{novel_id}/feedback`
- 作用：提交人工反馈，进入质量闭环。
- 请求体：`chapter_num|volume_no`, `feedback_type`, `rating`, `tags`, `comment`。
- 返回：已创建反馈记录。

### `GET /api/novels/{novel_id}/observability`
- 作用：统一返回质量报告、检查点、反馈与风险摘要，供前端监控面板使用。
- 参数：`limit`（默认 50）。
- `summary` 新增治理指标：
  - `closure_action_distribution`
  - `closure_action_oscillation_rate`
  - `abrupt_ending_score`
  - `abrupt_ending_risk`
  - `abrupt_ending_reasons`
  - `node_counts`
  - `node_latency_seconds`（按节点聚合 `p50/p95/max`）
  - `reason_code_distribution`
  - `hard_constraint_violation_rate`
  - `soft_constraint_warning_rate`
  - `review_over_correction_risk_rate`
  - `review_accept_minor_polish_rate`

### `GET /api/novels/{novel_id}/character-profiles`
- 作用：查看小说生成过程中逐章沉淀的角色硬形象画像。
- 返回：角色硬字段（含 `skin_tone`, `ethnicity`, 外观锚点、证据与置信度）。

### `GET /api/novels/{novel_id}/closure-report`
- 作用：返回最新的收官门禁状态（章节弹性、未回收项、收官动作）。
- 参数：`task_id`（可选）。
- 返回：`available`, `state(action/phase_mode/remaining_ratio/unresolved_count/must_close_items...)`。

### `GET /api/novels/{novel_id}/generation/status`
- 作用：查询统一调度后的生成状态（`creation_tasks` 为主，Redis 为实时补充）。
- 关键字段：
  - 顶层状态统一为：`queued | dispatching | running | paused | completed | failed | cancelled`
  - `phase`: 当前阶段（细粒度节点信息）
  - `progress`: 0~100
  - `message/error`

### `POST /api/novels/{novel_id}/generation/retry`
- 作用：失败/取消后一键重试，默认自动选择最新失败任务；也可指定 `task_id`。
- 请求体：`{ "task_id": "可选，指定失败任务ID" }`
- 行为：重试任务进入统一调度队列（状态 `queued`）。

### `POST /api/novels/{novel_id}/generation/pause`
- 作用：暂停任务并释放并发槽位（状态切换至 `paused`）。
- 参数：`task_id`（可选，默认取最新运行任务）。

### `POST /api/novels/{novel_id}/generation/resume`
- 作用：恢复任务并重新入队（状态切换至 `queued`）。
- 参数：`task_id`（可选）。

### `POST /api/novels/{novel_id}/generation/cancel`
- 作用：取消运行任务。
- 参数：`task_id`（可选）。

### `GET /api/novels/{novel_id}/generation/tasks`
- 作用：列出任务历史及错误分类信息。
- 关键字段：`status/error_code/error_category/retryable`。

### `GET /api/account/quota`
- 作用：查看当前账号套餐配额与本月已用量。
- 返回：`plan_key/max_concurrent_tasks/monthly_chapter_limit/monthly_token_limit/used_*/remaining_*`。
- 说明：并发上限是否生效由环境变量 `QUOTA_ENFORCE_CONCURRENCY_LIMIT` 控制；默认 `false`（不限制并发创建）。
- 月度限额由环境变量控制：
  - `QUOTA_FREE_MONTHLY_CHAPTER_LIMIT`
  - `QUOTA_FREE_MONTHLY_TOKEN_LIMIT`
  - `QUOTA_ADMIN_MONTHLY_CHAPTER_LIMIT`
  - `QUOTA_ADMIN_MONTHLY_TOKEN_LIMIT`

### `GET /api/account/ledger`
- 作用：查看任务级账本明细（token、章节、估算成本）。
- 参数：`limit`（默认 50）。

### `GET /api/account/notifications`
- 作用：通知中心数据源，汇总生成/重写完成、失败、取消事件。
- 参数：`limit`（默认 30）。

### `GET /api/novels/{novel_id}/versions/{version_id}/diff?compare_to={base_version_id}`
- 作用：对比两个版本的章节差异（章节级标题变化 + 内容相似度）。
- 返回：`summary(total_chapters/changed_chapters/change_ratio)` + `chapters[]`。

### `GET /api/admin/observability/summary`
- 作用：管理员聚合观测面板。
- 返回：
  - `model_error_rate`
  - `retry_hit_rate`
  - `review_overfix_risk_rate`
  - `node_latency_seconds.{node}.p50/p95/max`

### `GET /api/admin/settings/models`
- 作用：读取系统模型配置（Provider 列表、默认模型、回退顺序）。
- 说明：返回中会标记每个 Provider 的来源（`db|env`）和密钥来源；API Key 仅返回掩码。

### `PUT /api/admin/settings/models`
- 作用：全量保存模型配置（多 Provider、多模型、按类型默认模型）。
- 请求体：`providers[]`，字段包含 `provider_key/display_name/adapter_type/base_url/api_key/is_enabled/priority/models[]`。
- 规则：
  - 默认模型按类型唯一（`chat|embedding|image|video`）。
  - `api_key` 传 `null` 表示保留原值，传空字符串表示清空。
  - 若配置 `SYSTEM_SETTINGS_MASTER_KEY`，密钥加密入库；否则按明文入库并在 UI 风险提示。

### `GET /api/admin/settings/runtime`
- 作用：读取运行时配置（调度与配额）并返回每项来源（`db|env`）。
- 首期可配：
  - `creation_scheduler_enabled`
  - `creation_default_max_concurrent_tasks`
  - `creation_max_dispatch_batch`
  - `creation_worker_lease_ttl_seconds`
  - `creation_worker_heartbeat_seconds`
  - `quota_enforce_concurrency_limit`
  - `quota_free_monthly_chapter_limit`
  - `quota_free_monthly_token_limit`
  - `quota_admin_monthly_chapter_limit`
  - `quota_admin_monthly_token_limit`

### `PUT /api/admin/settings/runtime`
- 作用：保存运行时配置覆盖项。
- 请求体：`updates: { key: value|null }`；`null` 表示删除该覆盖并回退环境变量。

### `GET /api/admin/settings/effective`
- 作用：查看当前进程生效配置快照（调试用途）。
- 说明：返回聚合后的默认模型、回退顺序、运行时覆盖项；敏感字段均掩码。

## Novel 辅助端点

### `POST /api/novels/idea-framework`
- 作用：根据标题一键生成“可编辑创意框架”，用于首页/创建页快速填充。
- 请求体：`title`（必填），`target_language`、`genre`、`style`、`strategy`（可选）。
- 返回：`one_liner/premise/conflict/hook/selling_point/editable_framework`。

## Storyboard Endpoints

### `POST /api/storyboards`
- 作用：为已完结小说创建导演分镜项目（V2 固定绑定小说版本快照入口）。
- 关键入参：
  - `novel_id`（仅允许 `novel.status=completed`）
  - `source_novel_version_id`（可选；不传时锁定小说默认版本）
  - `mode`（`quick|professional`，默认 `quick`）
  - `genre_style_key` / `director_style_key`（可选，默认用推荐）
  - `auto_style_recommendation`（默认 true）
  - `target_episodes`
  - `target_episode_seconds`
  - `output_lanes`（默认 `vertical_feed + horizontal_cinematic`）
  - `professional_mode=true`
  - `audience_goal`
  - `copyright_assertion=true`（必填硬门槛）

### `POST /api/storyboards/{project_id}/preflight`
- 作用：执行硬门禁预检查（角色身份字段、源快照）。
- 入参：`force_refresh_snapshot`（默认 false）。
- 返回：`gate_status`、`missing_identity_fields_count`、`failed_identity_characters`、`snapshot_hash`。

### `POST /api/storyboards/{project_id}/runs`
- 作用：启动一次分镜运行（Run），由调度器按 lane 派发子任务。
- 幂等：支持 `Idempotency-Key` 请求头。
- 前置条件：项目必须先 `preflight_passed`，否则返回 `409 storyboard_preflight_required`。

### `GET /api/storyboards/{project_id}/runs`
- 作用：按创建时间倒序列出运行记录（含 lane 子状态）。

### `GET /api/storyboards/{project_id}/runs/{run_id}`
- 作用：读取单个 Run 聚合状态（`status/run_state/current_phase/progress` + `lanes[]`）。

### `GET /api/storyboards/{project_id}/runs/{run_id}/events`
- 作用：Run 实时状态 SSE 流（事件类型：`run_status`）。
- 用途：工作台实时刷新，不必仅依赖轮询。

### `POST /api/storyboards/{project_id}/runs/{run_id}/actions`
- 作用：运行控制（`pause|resume|cancel|retry`）。
- 请求体：`{"action":"pause|resume|cancel|retry"}`。
- 幂等：`retry` 支持 `Idempotency-Key`。

### `GET /api/storyboards/style-presets`
- 作用：获取内置双层风格库（题材风格 + 导演风格）。

### `POST /api/storyboards/style-recommendations`
- 入参：`novel_id`
- 作用：返回 AI 推荐 Top3 风格组合（含置信度和理由）。

### `GET /api/storyboards/{project_id}/versions`
- 作用：查看版本列表（含 `lane`, `is_default`, `is_final`, `quality_report_json`）。

### `POST /api/storyboards/{project_id}/versions/{version_id}/activate`
- 作用：切换默认版本（工作台读取源）。

### `POST /api/storyboards/{project_id}/versions/{version_id}/finalize`
- 作用：人工确认定稿（定稿后导出）。
- 额外门禁：必须先通过角色身份字段门禁（每角色 `skin_tone/ethnicity` 必填合法）。

### `GET /api/storyboards/{project_id}/versions/{version_id}/shots?episode_no=...`
- 作用：按版本读取镜头级分镜数据。

### `PUT /api/storyboards/{project_id}/versions/{version_id}/shots/{shot_id}`
- 作用：人工编辑镜头字段（定稿版本不可编辑，且镜头必须归属该版本）。

### `GET /api/storyboards/{project_id}/versions/{version_id}/character-cards`
- 作用：按版本读取角色主形象卡（V2 新表，版本绑定）。

### `PUT /api/storyboards/{project_id}/versions/{version_id}/character-cards/{card_id}`
- 作用：人工修正角色卡关键字段（含 `skin_tone/ethnicity`）。

### `GET /api/storyboards/{project_id}/characters?version_id=...&lane=...`
- 作用：读取角色主形象提示词（兼容旧表）。

### `POST /api/storyboards/{project_id}/characters/generate`
- 作用：手动重生角色主形象提示词（会回填到 V2 `character_cards` 与旧表）。

### `GET /api/storyboards/{project_id}/characters/export?version_id=...&lane=...&format=csv|json`
- 作用：导出角色主形象提示词（兼容接口）。
- 限制：仅 `is_final=true` 且身份字段门禁通过可导出。

### `POST /api/storyboards/{project_id}/versions/{version_id}/optimize`
- 作用：一键应用结构化修订建议，自动优化镜头可拍性与字段完整性。

### Storyboard Prompt 模板
- 位置：`app/prompts/templates/`
- 已外置模板示例：
  - `storyboard_style_recommend_reason.j2`
  - `storyboard_shot_action.j2`
  - `storyboard_shot_blocking.j2`
  - `storyboard_shot_performance_note.j2`
  - `storyboard_shot_continuity_anchor.j2`
  - `storyboard_rewrite_suggestion_*.j2`
  - `character_profile_increment_extract.j2`
  - `character_profile_merge_policy.j2`
  - `storyboard_character_master_prompt.j2`
  - `storyboard_character_negative_prompt.j2`
  - `storyboard_character_identity_gate_fail.j2`

### `GET /api/storyboards/{project_id}/versions/{version_id}/diff?compare_to=...`
- 作用：版本差异（新增/删除/修改镜头统计）。

### `POST /api/storyboards/{project_id}/versions/{version_id}/exports`
- 作用：提交异步导出任务（`csv|json|pdf`）。
- 幂等：支持 `Idempotency-Key`，同版本同格式重复请求可复用已有任务。

### `GET /api/storyboards/{project_id}/exports/{export_id}`
- 作用：查询导出任务状态；完成后返回签名下载地址。

### `GET /api/storyboards/{project_id}/exports/{export_id}/download?expires=...&sig=...`
- 作用：下载导出工件（签名校验 + 过期校验）。

### 兼容接口（保留）
- `POST /api/storyboards/{project_id}/generate`
- `GET /api/storyboards/{project_id}/status`
- `POST /api/storyboards/{project_id}/pause|resume|cancel|retry`
- `GET /api/storyboards/{project_id}/shots`
- `PUT /api/storyboards/{project_id}/shots/{shot_id}`
- `GET /api/storyboards/{project_id}/export/csv`

## 权限矩阵（RBAC + Resource Policy）

- 匿名可访问：
  - `GET /health`
  - `GET /api/presets`
  - `GET /api/presets/{category}`
  - Auth 白名单接口（register/login/verify/reset）
- 登录用户（`user`）：
  - `novel:read/create/update/delete/generate/rewrite`
  - 仅限本人资源（`novels.user_id == current_user.uuid`）
  - `storyboard:read/create/update/generate/finalize/export`
  - 仅限本人资源（`storyboard_projects.owner_user_uuid == current_user.uuid`）
- 管理员（`admin`）：
  - 全部用户权限 + `user:read`, `user:disable`
  - 可访问所有小说资源

## 离线评估脚本

### `scripts/evaluate_generation_metrics.py`
- 作用：跨小说输出聚合治理报告（动作振荡率、突兀结尾率、风险 Top 列表）。
- 用法：
```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/evaluate_generation_metrics.py
UV_CACHE_DIR=.uv-cache uv run python scripts/evaluate_generation_metrics.py --enforce-thresholds
```
