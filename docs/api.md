# API 文档补充（长篇生成能力）

## Longform Endpoints

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

### `GET /api/novels/{novel_id}/closure-report`
- 作用：返回最新的收官门禁状态（章节弹性、未回收项、收官动作）。
- 参数：`task_id`（可选）。
- 返回：`available`, `state(action/phase_mode/remaining_ratio/unresolved_count/must_close_items...)`。

### `GET /api/novels/{novel_id}/generation/status`
- 作用：查询实时生成状态（Redis 优先，DB 回退）。
- 关键字段：
  - `current_subtask`: `{ key, label, progress }` 稳定子任务对象。
  - `eta_seconds` / `eta_label`: 预计剩余时间（基于最近章节耗时平滑估算）。
  - `decision_state`:
    - `closure`: `phase_mode/action/must_close_coverage/threshold/unresolved_count/bridge_budget_left/reasons`
    - `pacing`: `mode(low|accelerated|closing_accelerated)/low_progress_streak/progress_signal/reasons`
    - `quality`: `review_score/factual_score/language_score/aesthetic_score/quality_passed/review_suggestions`
      - `consistency_scorecard`: 一致性结构化评分（blockers/warnings/categories/reason_codes）
      - `review_gate`: 审校门控结果（evidence_coverage/decision/over_correction_risk）
- 兼容字段：`subtask_key/subtask_label/subtask_progress` 继续保留，后续版本再下线。

### `POST /api/novels/{novel_id}/generation/retry`
- 作用：失败/取消后一键重试，默认自动选择最新失败任务；也可指定 `task_id`。
- 请求体：`{ "task_id": "可选，指定失败任务ID" }`
- 行为：从失败任务 `current_chapter` 继续提交新任务（`task_id` 会变化）。

## Novel 辅助端点

### `POST /api/novels/idea-framework`
- 作用：根据标题一键生成“可编辑创意框架”，用于首页/创建页快速填充。
- 请求体：`title`（必填），`target_language`、`genre`、`style`、`strategy`（可选）。
- 返回：`one_liner/premise/conflict/hook/selling_point/editable_framework`。

## 离线评估脚本

### `scripts/evaluate_generation_metrics.py`
- 作用：跨小说输出聚合治理报告（动作振荡率、突兀结尾率、风险 Top 列表）。
- 用法：
```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/evaluate_generation_metrics.py
```
