# Prompt Engineering

AI-JinShu 的提示词工程保持现有 `agents.py + render_prompt() + llm_contract` 主链不变，但在其上增加了 6 个治理层：

1. `Memory governance prompt pack`
2. `Reviewer / Finalizer / Progression extractor` 角色宪法
3. `Prompt section registry`
4. `Lightweight context selector`
5. `Style overlay`
6. `Prompt rationale / eval note`

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

## Context Selector

`app/services/memory/context.py` 中的 `select_context_candidates()` 用于在主上下文组装前对高噪声候选进行轻量筛选。

第一阶段只筛选：

- `knowledge_chunks`
- `recent_window`
- `story_bible_context` 内的扩展条目

它是 soft-fail 的：

- 选择失败时回退到现有 heuristic
- 不新增任务状态
- 不改变 graph 拓扑

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
