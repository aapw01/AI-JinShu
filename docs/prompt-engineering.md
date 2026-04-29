# Prompt Engineering

AI-JinShu 的提示词工程保持现有 `agents.py + render_prompt() + llm_contract` 主链不变，但在其上增加了 8 个治理层：

1. `Prompt asset registry`
2. `Memory governance prompt pack`
3. `Reviewer / Finalizer / Progression extractor` 角色宪法
4. `Prompt section registry`
5. `Context block selector`
6. `Character focus pack`
7. `Style overlay`
8. `Prompt rationale / eval note`

## 设计目标

- 不重写 LangGraph 主流程
- 不改前后端 API
- 在不明显拉低任务成功率的前提下，提高长篇一致性、审校稳定性和 prompt 可维护性

## 共享 Sections

共享 section 仍然使用 Jinja2 模板，路径位于：

- `app/prompts/templates/policy/*`
- `app/prompts/templates/role/*`
- `app/prompts/templates/style/*`
- `app/prompts/templates/contract/*`

渲染入口：

- `app.prompts.render_prompt_section()`
- `app.prompts.render_prompt_sections()`

当前第一批主要接入：

- `chapter_memory_structured.j2`
- `reviewer_factual_structured.j2`
- `reviewer_progression_structured.j2`
- `reviewer_structured.j2`
- `reviewer_combined.j2`
- `reviewer_aesthetic_structured.j2`
- `first_chapter.j2`
- `next_chapter.j2`
- `finalizer_polish.j2`
- `final_book_review.j2`

## Prompt Asset Registry

`app/prompts/registry.py` 为核心生成 prompt 提供稳定资产编号：

- `generation.chapter.writer@v2`
- `generation.chapter.finalizer@v2`
- `generation.review.combined@v1`
- `generation.outline.volume_batch@v1`

Registry 不替代 Jinja2 模板，只记录 `id/version/template/task_type/output_contract/context_policy`。模型调用日志和 runtime snapshot 应优先记录 `prompt_asset_id`、`prompt_version`、`prompt_template` 和 `prompt_hash`。

## Memory Governance

Memory policy 的核心原则：

- 只有正文中明确发生的事实才能进入后续强约束
- outline / segment plan / 卷规划属于目标，不属于已发生事实
- memory 与当前正文冲突时，以当前正文和已落库章节事实为准
- anti-repeat / transition constraints 只有在证据充分时才升级为 blocker

这套规则重点保护：

- `story_bible`
- `progression`
- `recent summary`
- `transition state`

## 角色宪法

当前有 5 个角色宪法：

- `writer`
- `reviewer`
- `finalizer`
- `progression_extractor`
- `fact_extractor`

角色宪法只负责角色纪律，不负责业务上下文。业务上下文仍由各模板本身和 `context.py` 负责。

## Context Block Selector

`app/services/memory/context.py` 中的 `select_context_candidates()` 用于在主上下文组装前对高噪声候选进行轻量筛选。`app/services/memory/context_blocks.py` 再把最终上下文分成：

- `required`
- `preferred`
- `optional`

预算不足时，系统保留 required，按优先级裁剪 preferred / optional，并把 `included_block_ids`、`dropped_block_ids`、`used_tokens` 写入 `context_selector_meta`。

它是 soft-fail 的：

- 选择失败时回退到现有 heuristic
- 不新增任务状态
- 不改变 graph 拓扑

## Runtime Snapshot

writer 节点会把每章的 prompt asset、prompt hash、上下文 block 选择、模型与诊断信息写入 `creation_tasks.resume_cursor_json.runtime_state.chapter_runtime_snapshots`。该快照用于失败复盘和重跑解释，不参与前端 API 形态。

## Generation Harness

`app/services/generation/harness/` 提供四个后端-only 的回归与诊断工具，不新增路由、数据库表或前端功能：

- `replay.py`：从 runtime snapshot 生成章节 replay bundle，用于定位某章使用的 prompt、上下文裁剪和诊断信息。
- `consistency_eval.py`：对固定一致性用例做 pass/fail 评分，先支持预计算的 blocker / warning 结果。
- `fact_ledger.py`：聚合 `StoryEntity`、`StoryFact`、`StoryEvent`、`StoryForeshadow`、`StoryRelation` 和角色记忆，形成全书事实账本。
- `context_budget.py`：用固定场景回放 context block token 预算选择，检查 required / preferred / optional 的保留与裁剪是否符合预期。

## Character Focus Pack

`app/services/memory/character_focus.py` 会为当前章节生成轻量人物约束包，来源包括：

- prewrite 中的人物设定、目标、动机、声纹；
- 当前章 outline 提到的人物、站位、冲突轴、关系变化；
- `novel_memory` 中的角色动态状态；
- `story_character_profiles` 中的硬形象锁定项。

该包写入 `context.character_focus_pack`，并作为 `character_focus_pack` context block 参与 token 预算选择。写作模板只读取 JSON，不新增 API、数据库字段或前端功能。

## Style Overlay

Style overlay 把“写作风格”从“结构任务”中拆开。当前支持：

- `style_web_novel_overlay`
- `style_literary_overlay`
- `style_fast_paced_overlay`
- `style_native_language_overlay`

writer 和 finalizer 会读取 style overlay；reviewer / extractor 当前不读取。

## 模板维护规则

关键模板头部必须保留三类信息：

- 模板职责
- 主要防的失败模式
- 最近一次关键调整原因或不可轻删的规则

如果要修改这些模板，先检查是否在修已知失败模式，避免只改 wording 却删掉护栏。
