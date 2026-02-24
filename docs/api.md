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
