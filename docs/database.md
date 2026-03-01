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
| `storyboard_projects` | 导演分镜项目主表 | `novel_id`, `owner_user_uuid`, `status`, `target_episodes`, `output_lanes`, `config_json(mode/风格推荐/已选风格)` |
| `storyboard_versions` | 双 lane 版本管理 | `storyboard_project_id`, `version_no`, `lane`, `is_default`, `is_final`, `quality_report_json` |
| `storyboard_shots` | 镜头级专业分镜明细 | `episode_no`, `scene_no`, `shot_no`, `blocking`, `motivation`, `performance_note`, `continuity_anchor` |
| `story_character_profiles` | 小说生成期逐章沉淀的角色硬形象画像 | `novel_id`, `character_key`, `skin_tone`, `ethnicity`, `evidence_json`, `confidence` |
| `storyboard_character_prompts` | 分镜版本下的角色主形象提示词产物 | `storyboard_project_id`, `storyboard_version_id`, `lane`, `character_key`, `master_prompt_text`, `negative_prompt_text` |
| `storyboard_tasks` | 分镜异步任务与状态 | `task_id`, `run_state`, `current_phase`, `current_lane`, `progress`, `error_*` |
| `storyboard_assertions` | 合规与定稿声明 | `storyboard_project_id`, `user_uuid`, `assertion_type`, `assertion_text` |

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
| `idx_storyboard_versions_project_lane_version` | `(storyboard_project_id, lane, version_no)` unique |
| `idx_storyboard_shots_version_episode_scene_shot` | `(storyboard_version_id, episode_no, scene_no, shot_no)` unique |
| `idx_story_character_profiles_novel_character` | `(novel_id, character_key)` unique |
| `idx_storyboard_character_prompts_version_lane_character` | `(storyboard_version_id, lane, character_key)` unique |
| `idx_storyboard_tasks_project_status` | `(storyboard_project_id, status)` |
| `idx_storyboard_assertions_project_type` | `(storyboard_project_id, assertion_type)` |
