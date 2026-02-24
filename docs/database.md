# 数据库设计补充（Phase 1 长篇能力）

## 新增核心表

| 表名 | 用途 | 关键字段 |
|---|---|---|
| `story_entities` | Story Bible 实体层 | `novel_id`, `entity_type`, `name`, `status`, `revision` |
| `story_facts` | 实体事实版本记录 | `entity_id`, `fact_type`, `value_json`, `chapter_from`, `chapter_to` |
| `story_events` | 章节事件与因果链 | `event_id`, `chapter_num`, `actors`, `causes`, `effects` |
| `story_foreshadows` | 伏笔生命周期 | `foreshadow_id`, `planted_chapter`, `resolved_chapter`, `state` |
| `story_snapshots` | 卷级快照 | `volume_no`, `chapter_end`, `snapshot_json` |
| `generation_checkpoints` | 任务恢复点 | `task_id`, `volume_no`, `chapter_num`, `node`, `state_json` |
| `quality_reports` | 质量评估记录 | `scope`, `scope_id`, `metrics_json`, `verdict` |
| `novel_feedback` | 人工反馈闭环 | `chapter_num`, `volume_no`, `feedback_type`, `rating`, `tags`, `comment` |

## 索引策略

| 索引名 | 字段 |
|---|---|
| `idx_story_entities_novel_type_name` | `(novel_id, entity_type, name)` |
| `idx_story_facts_novel_entity_type` | `(novel_id, entity_id, fact_type)` |
| `idx_story_events_novel_chapter` | `(novel_id, chapter_num)` |
| `idx_story_foreshadows_novel_state` | `(novel_id, state)` |
| `idx_story_snapshots_novel_volume` | `(novel_id, volume_no)` |
| `idx_generation_checkpoints_task_node` | `(task_id, node)` |
| `idx_quality_reports_novel_scope_scopeid` | `(novel_id, scope, scope_id)` |
| `idx_novel_feedback_novel_chapter_volume` | `(novel_id, chapter_num, volume_no)` |
