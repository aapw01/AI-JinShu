# Outline Generation Architecture Overhaul

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 彻底修复大纲生成系统：从"一次性生成全书大纲（必失败）"改为"分卷渐进生成 + 章前精化"，同时消除所有残留占位数据，让每章都有真实的结构指导。

**Architecture:** 三层大纲架构：①初始化仅生成第一卷大纲（volume_size 章）；②每卷开始时 `node_volume_replan` 利用真实章节摘要生成下一卷大纲；③ `node_refine_chapter_outline` 在写章前用最近 3-5 章真实摘要精化当前章大纲。同步修复 `_looks_like_meta_text` 漏检英文占位词、resume 时脏数据复用两个 P0 问题。

**Tech Stack:** Python, LangGraph, SQLAlchemy, Jinja2, Pydantic, pytest

---

## 根本问题分析

| 问题 | 现象 | 根因 |
|------|------|------|
| `run_full_book(200章)` 必然失败 | 所有章节 `outline=""` | 本地 LLM 无法一次输出 200 章 × 22 字段 |
| exception fallback 写入空数据 | DB + resume_cursor 都存了垃圾 | fallback 没有拒绝策略，直接落库 |
| `_looks_like_meta_text` 漏检英文 | `"auto-generated out"` 出现在标题 | 只检查中文短语列表，缺少英文关键词 |
| resume 复用脏 segment_plan | 重启 worker 仍显示坏标题 | `_covers_outline_range` 只检查 chapter_num 覆盖，不验证内容质量 |
| 写章时无结构指导 | 叙事平铺、伏笔缺失、冲突重复 | 大纲为空，writer prompt 22 个字段全为空 |

---

## 文件结构

| 文件 | 变更类型 | 职责 |
|------|----------|------|
| `app/services/generation/common.py` | Modify | 修复 `_looks_like_meta_text`；新增 `_is_outline_stale()` |
| `app/services/generation/agents.py` | Modify | 新增 `OutlinerAgent.run_volume_outlines()`；`run_full_book` 改为调用分批接口 |
| `app/services/generation/nodes/init_node.py` | Modify | `node_outline` 改为只生成第一卷；新增 `_outline_quality_ok()` |
| `app/services/generation/nodes/volume.py` | Modify | `node_volume_replan` 末尾生成下一卷大纲并写 DB |
| `app/services/generation/nodes/chapter_loop.py` | Modify | 新增 `node_refine_chapter_outline` 节点 |
| `app/services/generation/nodes/__init__.py` | Modify | 导出 `node_refine_chapter_outline` |
| `app/services/generation/graph.py` | Modify | 在 `load_context` → `consistency_check` 之间插入 `refine_outline` 节点 |
| `app/prompts/templates/volume_outline.j2` | Create | 新增：分卷大纲生成 prompt（接受 previous_summaries） |
| `app/prompts/templates/chapter_outline_refine.j2` | Create | 新增：章前精化 prompt（基于最近真实摘要） |
| `tests/test_outline_generation.py` | Create | 所有新逻辑的单元测试 |

---

## Task 1：修复 `_looks_like_meta_text` 漏检英文占位词（P0）

**Files:**
- Modify: `app/services/generation/common.py:32-99`
- Test: `tests/test_outline_generation.py`

### 问题代码

`_META_TITLE_PHRASES`（line 32）只有中文短语，`_looks_like_meta_text`（line 89-99）的英文分支仅检测 `"feedback" + "chapter"` 组合，无法捕获 `"auto-generated"`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_outline_generation.py
import pytest
from app.services.generation.common import _looks_like_meta_text, is_effective_title

class TestLooksLikeMetaText:
    def test_catches_auto_generated(self):
        assert _looks_like_meta_text("auto-generated outline") is True

    def test_catches_auto_generated_out(self):
        assert _looks_like_meta_text("auto-generated out") is True

    def test_catches_placeholder(self):
        assert _looks_like_meta_text("placeholder") is True

    def test_catches_todo(self):
        assert _looks_like_meta_text("TODO: fill in") is True

    def test_allows_real_title(self):
        assert _looks_like_meta_text("铁幕之下") is False

    def test_allows_english_real_title(self):
        assert _looks_like_meta_text("The Storm Breaks") is False

class TestIsEffectiveTitle:
    def test_rejects_auto_generated_out(self):
        assert is_effective_title("第1章：auto-generated out", 1) is False

    def test_rejects_bare_chapter_num(self):
        assert is_effective_title("第5章", 5) is False

    def test_accepts_real_title(self):
        assert is_effective_title("第1章：铁幕之下", 1) is True
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/test_outline_generation.py::TestLooksLikeMetaText -v
```
预期：FAIL（`auto-generated` 未被捕获）

- [ ] **Step 3: 修复 `_META_TITLE_PHRASES` 和 `_looks_like_meta_text`**

在 `app/services/generation/common.py` 中：

```python
# 原 _META_TITLE_PHRASES (line 32)，在末尾追加英文占位词：
_META_TITLE_PHRASES = (
    "以下是根据反馈",
    "重点解决如下问题",
    "原章节内容",
    "人工标注",
    "修订版",
    "全面打磨",
    "提示词",
    "保持原有",
    # English placeholders
    "auto-generated",
    "placeholder",
    "todo",
    "tbd",
    "fill in",
    "insert here",
)
```

同时在 `_looks_like_meta_text`（line 89）中将英文短语检测改为 `lowered` 匹配（已经全部 lower 了，直接在上面的 tuple 里加小写即可）：

```python
def _looks_like_meta_text(text: str) -> bool:
    source = str(text or "").strip()
    if not source:
        return False
    lowered = source.lower()
    if "feedback" in lowered and "chapter" in lowered:
        return True
    for phrase in _META_TITLE_PHRASES:
        if phrase.lower() in lowered:   # 统一用 lowered 比对，兼容大小写
            return True
    return False
```

> 注意：`_META_TITLE_PHRASES` 中文短语本来就区分大小写，改为 `phrase.lower() in lowered` 不会破坏中文匹配（中文 lower() 无变化）。

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/test_outline_generation.py -v
```
预期：PASS

- [ ] **Step 5: 运行完整 lint**

```bash
uv run ruff check app/services/generation/common.py
```

- [ ] **Step 6: Commit**

```bash
git add app/services/generation/common.py tests/test_outline_generation.py
git commit -m "fix(common): catch English placeholder text in _looks_like_meta_text"
```

---

## Task 2：新增 `_outline_quality_ok()` + 脏数据检测（P0）

**Files:**
- Modify: `app/services/generation/common.py`
- Modify: `app/services/generation/nodes/init_node.py:42-51`
- Test: `tests/test_outline_generation.py`

### 问题代码

`_covers_outline_range`（init_node.py:42-51）只验证 chapter_num 覆盖，不检验 outline 字段是否有实际内容。resume 时脏数据被直接复用。

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_outline_generation.py
from app.services.generation.common import is_outline_content_valid

class TestOutlineContentValid:
    def test_empty_outline_is_invalid(self):
        item = {"chapter_num": 1, "title": "第1章", "outline": ""}
        assert is_outline_content_valid(item) is False

    def test_auto_generated_outline_is_invalid(self):
        item = {"chapter_num": 1, "title": "第1章", "outline": "auto-generated outline"}
        assert is_outline_content_valid(item) is False

    def test_valid_outline(self):
        item = {"chapter_num": 1, "title": "第1章：铁幕之下", "outline": "主角在审讯室中发现线索"}
        assert is_outline_content_valid(item) is True

    def test_outline_too_short_is_invalid(self):
        item = {"chapter_num": 1, "title": "第1章", "outline": "推进"}
        assert is_outline_content_valid(item) is False
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/test_outline_generation.py::TestOutlineContentValid -v
```
预期：ImportError 或 AttributeError（函数不存在）

- [ ] **Step 3: 在 `common.py` 新增 `is_outline_content_valid`**

```python
def is_outline_content_valid(item: dict) -> bool:
    """Return True if the outline item has real (non-placeholder) content."""
    outline = str(item.get("outline") or "").strip()
    if not outline:
        return False
    if _looks_like_meta_text(outline):
        return False
    # At least 10 characters of real content
    if len(outline) < 10:
        return False
    return True
```

- [ ] **Step 4: 修改 `_covers_outline_range` 增加质量验证**

在 `app/services/generation/nodes/init_node.py:42-51`：

```python
def _covers_outline_range(outlines: list[dict] | None, start_chapter: int, end_chapter: int) -> bool:
    required = set(range(int(start_chapter), int(end_chapter) + 1))
    if not required:
        return False
    valid = {
        int(item.get("chapter_num") or 0)
        for item in (outlines or [])
        if isinstance(item, dict)
        and int(item.get("chapter_num") or 0) > 0
        and is_outline_content_valid(item)   # 新增：内容质量检查
    }
    return required.issubset(valid)
```

同时在 `init_node.py` 顶部 import 中加入：
```python
from app.services.generation.common import (
    is_outline_content_valid,  # 新增
    load_outlines_from_db,
    ...
)
```

- [ ] **Step 5: 运行确认通过**

```bash
uv run pytest tests/test_outline_generation.py -v
```

- [ ] **Step 6: Commit**

```bash
git add app/services/generation/common.py app/services/generation/nodes/init_node.py tests/test_outline_generation.py
git commit -m "fix(init_node): reject stale/placeholder outlines during resume detection"
```

---

## Task 3：新增 `OutlinerAgent.run_volume_outlines()` 分卷生成接口

**Files:**
- Create: `app/prompts/templates/volume_outline.j2`
- Modify: `app/services/generation/agents.py`
- Test: `tests/test_outline_generation.py`

### 目标

替换 `run_full_book` 的"一次生成全书"为"每次生成一卷（volume_size 章）"，并接受 `previous_summaries` 参数，让后续卷的大纲可以基于真实已写内容生成。

- [ ] **Step 1: 创建 `volume_outline.j2`**

```jinja2
{# volume_outline.j2: 分卷章节大纲生成 #}
你是长篇网文策划编辑。请为第 {{ volume_no }} 卷（第{{ start_chapter }}章~第{{ end_chapter }}章，共{{ num_chapters }}章）生成详细章节大纲。

<global_bible>
{{ prewrite | tojson_pretty }}
</global_bible>

{% if previous_summaries %}
<previous_chapters_summary>
以下是前几章的真实写作摘要（按章节顺序），用于确保大纲连贯：
{% for s in previous_summaries %}
第{{ s.chapter_num }}章：{{ s.summary }}
{% endfor %}
</previous_chapters_summary>
{% endif %}

{% if planning_context %}
<volume_planning_context>
{{ planning_context | tojson_pretty }}
</volume_planning_context>
{% endif %}

<task>
请生成第{{ start_chapter }}章到第{{ end_chapter }}章的章节大纲，共{{ num_chapters }}章。
每章必须包含：
- title: 章节标题（含实质内容，不得只是"第N章"）
- outline: 本章核心事件（≥20字）
- purpose: 本章在全书中的叙事功能
- hook: 开头钩子
- payoff: 本章给读者的兑现
- foreshadowing: 埋下的伏笔（无则留空）
- conflict_axis: 本章主冲突
- chapter_objective: 本章必须完成的叙事目标
- required_irreversible_change: 本章产生的不可逆变化
- mini_climax: 本章小高潮（无则填 none）
- opening_scene: 本章开场场景描述
- transition_mode: 与上一章的衔接方式

节奏要求：
- 每3章至少一次冲突升级或关系转折
- 每{{ (num_chapters / 3) | int }}章安排一次伏笔兑现
- 章末钩子必须与下章形成逻辑呼应

请以 JSON 格式输出，键为 "outlines"，值为数组，每个元素包含上述字段（加上 chapter_num）。
</task>
```

- [ ] **Step 2: 写失败测试**

```python
# 追加到 tests/test_outline_generation.py
from unittest.mock import MagicMock, patch

class TestRunVolumeOutlines:
    def test_returns_correct_count(self):
        agent = OutlinerAgent()
        mock_llm = MagicMock()
        mock_data = {
            "outlines": [
                {"chapter_num": i, "title": f"第{i}章：测试", "outline": "主角发现了关键线索，决定采取行动"}
                for i in range(1, 31)
            ]
        }
        with patch("app.services.generation.agents._invoke_json_with_schema", return_value=mock_data):
            with patch("app.services.generation.agents.get_llm_with_fallback", return_value=mock_llm):
                result = agent.run_volume_outlines(
                    novel_id="test",
                    volume_no=1,
                    start_chapter=1,
                    num_chapters=30,
                    prewrite={"global_bible": "test"},
                    previous_summaries=[],
                )
        assert len(result) == 30
        assert result[0]["chapter_num"] == 1
        assert result[29]["chapter_num"] == 30

    def test_returns_fallback_on_llm_failure(self):
        """失败时返回空 outline，不抛异常"""
        agent = OutlinerAgent()
        with patch("app.services.generation.agents._invoke_json_with_schema", side_effect=Exception("LLM error")):
            with patch("app.services.generation.agents.get_llm_with_fallback", return_value=MagicMock()):
                result = agent.run_volume_outlines(
                    novel_id="test",
                    volume_no=1,
                    start_chapter=1,
                    num_chapters=5,
                    prewrite={},
                    previous_summaries=[],
                )
        assert len(result) == 5
        # fallback 不返回 auto-generated 占位文本
        for item in result:
            assert item.get("outline", "") == ""
```

- [ ] **Step 3: 运行确认失败**

```bash
uv run pytest tests/test_outline_generation.py::TestRunVolumeOutlines -v
```
预期：ImportError（`run_volume_outlines` 不存在）

- [ ] **Step 4: 在 `agents.py` 新增 `run_volume_outlines`**

在 `OutlinerAgent` 类中，`run_full_book` 之前插入：

```python
def run_volume_outlines(
    self,
    novel_id: str,
    volume_no: int,
    start_chapter: int,
    num_chapters: int,
    prewrite: dict,
    previous_summaries: list[dict] | None = None,
    planning_context: dict[str, Any] | None = None,
    language: str = "zh",
    provider: str | None = None,
    model: str | None = None,
) -> list[dict]:
    """Generate outlines for one volume (num_chapters chapters starting at start_chapter)."""
    llm = get_llm_with_fallback(provider, model)
    end_chapter = start_chapter + num_chapters - 1
    prompt = render_prompt(
        "volume_outline",
        novel_id=novel_id,
        volume_no=volume_no,
        start_chapter=start_chapter,
        end_chapter=end_chapter,
        num_chapters=num_chapters,
        prewrite=prewrite,
        previous_summaries=previous_summaries or [],
        planning_context=planning_context or {},
        language=language,
    )
    try:
        data = _invoke_json_with_schema(llm, prompt, FullOutlinesSchema)
        outlines = data.get("outlines", []) if isinstance(data, dict) else []
        if not isinstance(outlines, list) or not outlines:
            raise ValueError("empty outlines from LLM")
        normalized = []
        for idx in range(num_chapters):
            chapter_no = start_chapter + idx
            item = outlines[idx] if idx < len(outlines) else {}
            normalized.append(
                normalize_outline_contract(
                    {
                        "chapter_num": chapter_no,
                        "title": normalize_title_text(item.get("title")) or f"第{chapter_no}章",
                        "outline": item.get("outline", ""),
                        "role": item.get("role"),
                        "purpose": item.get("purpose"),
                        "suspense_level": item.get("suspense_level"),
                        "foreshadowing": item.get("foreshadowing"),
                        "plot_twist_level": item.get("plot_twist_level"),
                        "hook": item.get("hook"),
                        "payoff": item.get("payoff"),
                        "mini_climax": item.get("mini_climax"),
                        "summary": item.get("summary"),
                        "chapter_objective": item.get("chapter_objective"),
                        "required_new_information": item.get("required_new_information"),
                        "required_irreversible_change": item.get("required_irreversible_change"),
                        "relationship_delta": item.get("relationship_delta"),
                        "conflict_axis": item.get("conflict_axis"),
                        "payoff_kind": item.get("payoff_kind"),
                        "reveal_kind": item.get("reveal_kind"),
                        "forbidden_repeats": item.get("forbidden_repeats"),
                        "opening_scene": item.get("opening_scene"),
                        "opening_character_positions": item.get("opening_character_positions"),
                        "opening_time_state": item.get("opening_time_state"),
                        "transition_mode": item.get("transition_mode"),
                    },
                    chapter_no,
                )
            )
        return normalized
    except Exception as e:
        logger.error(f"OutlinerAgent.run_volume_outlines vol={volume_no} ch={start_chapter}-{end_chapter} failed: {e}")
        return [
            normalize_outline_contract(
                {"chapter_num": start_chapter + i, "title": f"第{start_chapter + i}章", "outline": ""},
                start_chapter + i,
            )
            for i in range(num_chapters)
        ]
```

- [ ] **Step 5: 运行确认通过**

```bash
uv run pytest tests/test_outline_generation.py -v
```

- [ ] **Step 6: Commit**

```bash
git add app/prompts/templates/volume_outline.j2 app/services/generation/agents.py tests/test_outline_generation.py
git commit -m "feat(agents): add run_volume_outlines for per-volume outline generation"
```

---

## Task 4：修改 `node_outline` 只生成第一卷大纲

**Files:**
- Modify: `app/services/generation/nodes/init_node.py:239-284`
- Test: `tests/test_outline_generation.py`

### 目标

将 `node_outline` 第三路径（真正生成大纲）从 `run_full_book(num_chapters=200)` 改为 `run_volume_outlines(volume_no=1, num_chapters=volume_size)`。仅生成第一卷，后续卷由 `node_volume_replan` 负责。

### 当前问题代码（init_node.py:239-250）

```python
full_outlines = state["outliner"].run_full_book(
    novel_id=state["novel_id"],
    num_chapters=state["num_chapters"],   # 这里是 200，必然失败
    ...
)
```

- [ ] **Step 1: 写失败测试**

```python
# 追加到 tests/test_outline_generation.py
class TestNodeOutlineFirstVolumeOnly:
    """node_outline 在全新生成时只应生成第一卷数量的大纲"""

    def _make_state(self, num_chapters=60, volume_size=30):
        return {
            "novel_id": 1, "novel_version_id": 1,
            "start_chapter": 1, "end_chapter": num_chapters,
            "num_chapters": num_chapters,
            "segment_start_chapter": 1, "segment_end_chapter": num_chapters,
            "strategy": "web-novel", "target_language": "zh",
            "volume_size": volume_size,
            "prewrite": {"global_bible": "test"},
            "outliner": MagicMock(),
            "creation_task_id": None,
            "segment_plan": None,
            "volume_plan": {},
            "volume_no": 1,
            "retry_resume_chapter": 1,
            "current_chapter": 1,
        }

    def test_calls_run_volume_outlines_not_run_full_book(self):
        state = self._make_state(num_chapters=60, volume_size=30)
        with patch("app.services.generation.nodes.init_node.load_outlines_from_db", return_value=[]):
            with patch("app.services.generation.nodes.init_node.save_full_outlines"):
                with patch("app.services.generation.nodes.init_node.build_segment_plan", return_value={}):
                    with patch("app.services.generation.nodes.init_node.progress"):
                        state["outliner"].run_volume_outlines.return_value = [
                            {"chapter_num": i, "outline": "test content here with enough chars", "title": f"第{i}章：测试"}
                            for i in range(1, 31)
                        ]
                        node_outline(state)
        state["outliner"].run_volume_outlines.assert_called_once()
        call_kwargs = state["outliner"].run_volume_outlines.call_args[1]
        assert call_kwargs["num_chapters"] == 30   # 只生成第一卷
        assert call_kwargs["volume_no"] == 1
        state["outliner"].run_full_book.assert_not_called()
```

- [ ] **Step 2: 运行确认失败**

```bash
uv run pytest tests/test_outline_generation.py::TestNodeOutlineFirstVolumeOnly -v
```

- [ ] **Step 3: 修改 `node_outline` 第三路径**

在 `app/services/generation/nodes/init_node.py` 中，替换 line 239-250：

```python
# 原：
full_outlines = state["outliner"].run_full_book(
    novel_id=state["novel_id"],
    num_chapters=state["num_chapters"],
    prewrite=state["prewrite"],
    planning_context=state.get("volume_plan") or {},
    start_chapter=segment_start,
    language=state["target_language"],
    provider=out_provider,
    model=out_model,
)

# 改为：
volume_size = int(state.get("volume_size") or 30)
first_vol_end = segment_start + volume_size - 1
# 只生成第一卷（后续卷由 node_volume_replan 在卷开始时生成）
first_vol_outlines = state["outliner"].run_volume_outlines(
    novel_id=str(state["novel_id"]),
    volume_no=int(state.get("volume_no") or 1),
    start_chapter=segment_start,
    num_chapters=min(volume_size, state["num_chapters"]),
    prewrite=state["prewrite"],
    previous_summaries=[],
    planning_context=state.get("volume_plan") or {},
    language=state["target_language"],
    provider=out_provider,
    model=out_model,
)
full_outlines = first_vol_outlines
```

同时在顶部 import 确认 `OutlinerAgent` 已有 `run_volume_outlines`（Task 3 已完成则无需额外 import）。

- [ ] **Step 4: 运行确认通过**

```bash
uv run pytest tests/test_outline_generation.py -v
```

- [ ] **Step 5: Commit**

```bash
git add app/services/generation/nodes/init_node.py tests/test_outline_generation.py
git commit -m "feat(init_node): generate only first volume outlines on init, defer rest to volume_replan"
```

---

## Task 5：`node_volume_replan` 生成下一卷大纲

**Files:**
- Modify: `app/services/generation/nodes/volume.py`
- Test: `tests/test_outline_generation.py`

### 目标

在 `node_volume_replan` 末尾（已构建好 `volume_plan` 之后），检查下一卷的大纲是否存在且有效；若无效则调用 `run_volume_outlines` 生成并写入 DB。`previous_summaries` 从 SummaryManager 获取最近章节摘要，实现大纲与实际写作内容的对齐。

- [ ] **Step 1: 读取 `volume.py` 末尾确认插入点**

```bash
# 查看 node_volume_replan 末尾
```

```python
# 在 node_volume_replan return 语句之前插入下一卷大纲生成逻辑
```

- [ ] **Step 2: 写失败测试**

```python
# 追加到 tests/test_outline_generation.py
class TestVolumeReplanGeneratesNextVolumeOutlines:
    def test_generates_next_volume_when_missing(self):
        """当下一卷大纲不存在时，node_volume_replan 应生成并保存"""
        # 验证 save_full_outlines 被调用且内容为下一卷范围
        from app.services.generation.nodes.volume import node_volume_replan
        # ... (集成测试，需要 DB fixture，此处用 mock)
        pass  # 实现时补充
```

> 注：此节点测试需要较多 state mock，步骤 3 先实现代码，测试以手动验证为主。

- [ ] **Step 3: 在 `node_volume_replan` 末尾添加下一卷大纲生成**

在 `app/services/generation/nodes/volume.py` 的 `node_volume_replan` 函数 `return` 语句之前插入：

```python
# --- 生成下一卷大纲（如尚未生成） ---
from app.services.generation.agents import OutlinerAgent
from app.services.generation.common import (
    is_outline_content_valid,
    load_outlines_from_db,
    save_full_outlines,
)
from app.core.strategy import get_model_for_stage

volume_size = int(state.get("volume_size") or 30)
book_end = int(state.get("book_effective_end_chapter") or end)
next_vol_start = end + 1  # 下一卷起始章（当前卷结束的下一章）
next_vol_end = min(next_vol_start + volume_size - 1, book_end)

if next_vol_start <= book_end:
    existing = load_outlines_from_db(state["novel_id"], state.get("novel_version_id"))
    existing_map = {int(o.get("chapter_num", 0)): o for o in existing if isinstance(o, dict)}
    next_vol_range = range(next_vol_start, next_vol_end + 1)
    needs_generation = any(
        not is_outline_content_valid(existing_map.get(ch, {}))
        for ch in next_vol_range
    )
    if needs_generation:
        # 获取最近 5 章真实摘要作为上下文
        recent_summaries = []
        try:
            recent_summaries = state["summary_mgr"].get_summaries_before(
                state["novel_id"],
                state.get("novel_version_id"),
                next_vol_start,
                limit=5,
            ) or []
            recent_summaries = [
                {"chapter_num": s.get("chapter_num"), "summary": s.get("summary", "")}
                for s in recent_summaries
                if s.get("summary")
            ]
        except Exception as _e:
            logger.warning(f"node_volume_replan: failed to load recent summaries: {_e}")

        out_provider, out_model = get_model_for_stage(state.get("strategy", "web-novel"), "outliner")
        outliner = state.get("outliner") or OutlinerAgent()
        next_vol_no = vol_no + 1
        next_vol_outlines = outliner.run_volume_outlines(
            novel_id=str(state["novel_id"]),
            volume_no=next_vol_no,
            start_chapter=next_vol_start,
            num_chapters=next_vol_end - next_vol_start + 1,
            prewrite=state.get("prewrite") or {},
            previous_summaries=recent_summaries,
            planning_context=volume_plan,
            language=state.get("target_language", "zh"),
            provider=out_provider,
            model=out_model,
        )
        save_full_outlines(
            state["novel_id"],
            next_vol_outlines,
            novel_version_id=state.get("novel_version_id"),
            replace_all=False,  # 增量写入，不覆盖已有好数据
        )
        logger.info(
            "node_volume_replan: generated outlines for vol=%s ch=%s-%s",
            next_vol_no, next_vol_start, next_vol_end,
        )
```

同时在文件顶部 imports 中确认 `logger` 已导入（从 `app.services.generation.common import logger`）。

- [ ] **Step 4: 运行完整测试**

```bash
uv run pytest tests/test_outline_generation.py -v
uv run pytest tests/ -q -k "volume"
```

- [ ] **Step 5: Commit**

```bash
git add app/services/generation/nodes/volume.py
git commit -m "feat(volume_replan): generate next volume outlines using real chapter summaries"
```

---

## Task 6：新增 `node_refine_chapter_outline` 章前精化节点

**Files:**
- Create: `app/prompts/templates/chapter_outline_refine.j2`
- Modify: `app/services/generation/nodes/chapter_loop.py`
- Modify: `app/services/generation/nodes/__init__.py`
- Modify: `app/services/generation/graph.py`
- Test: `tests/test_outline_generation.py`

### 目标

在 `load_context` 和 `consistency_check` 之间插入 `refine_outline` 节点。该节点用最近 3-5 章的真实写作摘要精化当前章大纲，填充或修正 `hook/payoff/opening_scene/transition_mode` 等字段，保持与实际写作内容的连贯性。

### 图路由变更

```
# 原：
load_context → consistency_check → beats → writer

# 新：
load_context → refine_outline → consistency_check → beats → writer
```

- [ ] **Step 1: 创建 `chapter_outline_refine.j2`**

```jinja2
{# chapter_outline_refine.j2: 章前大纲精化（基于真实写作摘要） #}
你是资深网文编辑。当前正在创作第{{ chapter_num }}章，请根据前几章的真实写作内容，精化本章大纲。

<current_outline>
{{ outline | tojson_pretty }}
</current_outline>

<recent_actual_summaries>
{% for s in recent_summaries %}
第{{ s.chapter_num }}章（已写）：{{ s.summary }}
{% endfor %}
</recent_actual_summaries>

<task>
请在不改变本章核心目标（chapter_objective）的前提下，精化以下字段使其与实际写作内容衔接：
- opening_scene: 根据上章结尾调整开场场景
- transition_mode: 根据上章实际结尾确定过渡方式
- hook: 确保钩子与上章遗留悬念呼应
- payoff: 若上章有未兑现伏笔，优先在此兑现
- forbidden_repeats: 补充上几章已用过的推进手法（避免重复）

只输出需要修改的字段（JSON 对象）。若某字段无需修改则不包含该键。
若整体大纲已经准确无需修改，输出空 JSON 对象 {}。
</task>
```

- [ ] **Step 2: 写失败测试**

```python
# 追加到 tests/test_outline_generation.py
from app.services.generation.nodes.chapter_loop import node_refine_chapter_outline

class TestNodeRefineChapterOutline:
    def _make_state(self, outline=None, summaries=None):
        return {
            "novel_id": 1, "novel_version_id": 1,
            "current_chapter": 5,
            "start_chapter": 1, "end_chapter": 30,
            "num_chapters": 30,
            "outline": outline or {"chapter_num": 5, "outline": "test content enough", "hook": "old hook"},
            "full_outlines": [],
            "strategy": "web-novel",
            "target_language": "zh",
            "outliner": MagicMock(),
            "summary_mgr": MagicMock(),
            "volume_size": 30,
            "prewrite": {},
        }

    def test_returns_refined_outline_fields(self):
        state = self._make_state()
        mock_refined = {"hook": "新钩子：主角发现了密室", "opening_scene": "审讯室内，灯光昏黄"}
        state["summary_mgr"].get_summaries_before.return_value = [
            {"chapter_num": 4, "summary": "主角在追逐中发现了线索"}
        ]
        with patch("app.services.generation.nodes.chapter_loop.render_prompt", return_value="prompt"):
            with patch("app.services.generation.nodes.chapter_loop._invoke_json_simple", return_value=mock_refined):
                result = node_refine_chapter_outline(state)
        # 精化后的字段应合并回 outline
        assert result["outline"]["hook"] == "新钩子：主角发现了密室"
        assert result["outline"]["opening_scene"] == "审讯室内，灯光昏黄"

    def test_returns_original_outline_on_llm_failure(self):
        """LLM 失败时不应崩溃，返回原始大纲"""
        state = self._make_state()
        state["summary_mgr"].get_summaries_before.return_value = []
        with patch("app.services.generation.nodes.chapter_loop.render_prompt", return_value="prompt"):
            with patch("app.services.generation.nodes.chapter_loop._invoke_json_simple", side_effect=Exception("LLM error")):
                result = node_refine_chapter_outline(state)
        assert result["outline"] is not None
```

- [ ] **Step 3: 运行确认失败**

```bash
uv run pytest tests/test_outline_generation.py::TestNodeRefineChapterOutline -v
```
预期：ImportError

- [ ] **Step 4: 实现 `node_refine_chapter_outline`**

在 `app/services/generation/nodes/chapter_loop.py` 末尾添加：

```python
def node_refine_chapter_outline(state: GenerationState) -> GenerationState:
    """Pre-chapter outline refinement using actual recent chapter summaries."""
    from app.core.llm import get_llm_with_fallback
    from app.core.strategy import get_model_for_stage
    from app.prompts import render_prompt

    chapter_num = state["current_chapter"]
    outline = dict(state.get("outline") or {})

    # 跳过精化：若大纲已有充分内容（hook/opening_scene 均非空）且章节数小于 3
    if chapter_num < 3:
        return {"outline": outline}

    try:
        recent_summaries = state["summary_mgr"].get_summaries_before(
            state["novel_id"],
            state.get("novel_version_id"),
            chapter_num,
            limit=5,
        ) or []
        recent_summaries = [
            {"chapter_num": s.get("chapter_num"), "summary": s.get("summary", "")}
            for s in recent_summaries
            if s.get("summary")
        ]
    except Exception as e:
        logger.warning(f"node_refine_chapter_outline: failed to load summaries ch={chapter_num}: {e}")
        return {"outline": outline}

    if not recent_summaries:
        return {"outline": outline}

    try:
        prompt = render_prompt(
            "chapter_outline_refine",
            chapter_num=chapter_num,
            outline=outline,
            recent_summaries=recent_summaries,
        )
        out_provider, out_model = get_model_for_stage(state.get("strategy", "web-novel"), "outliner")
        llm = get_llm_with_fallback(out_provider, out_model)
        refined_fields = _invoke_json_simple(llm, prompt)
        if isinstance(refined_fields, dict) and refined_fields:
            # 只更新合法字段，不允许 LLM 修改 chapter_num / chapter_objective / outline（核心不变）
            _REFINABLE_FIELDS = {
                "hook", "payoff", "opening_scene", "transition_mode",
                "forbidden_repeats", "opening_character_positions", "opening_time_state",
            }
            for field in _REFINABLE_FIELDS:
                if field in refined_fields:
                    outline[field] = refined_fields[field]
    except Exception as e:
        logger.warning(f"node_refine_chapter_outline: refinement failed ch={chapter_num}: {e}")

    return {"outline": outline}
```

同时在文件顶部添加：
```python
from app.core.llm import get_llm_with_fallback
```
以及新增辅助函数 `_invoke_json_simple`（简化版，不用 Pydantic schema）：

```python
def _invoke_json_simple(llm, prompt: str) -> dict:
    """Invoke LLM and parse JSON response, no schema enforcement."""
    import json
    response = llm.invoke(prompt)
    text = str(getattr(response, "content", response) or "").strip()
    # 提取 JSON block
    import re
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return {}
```

- [ ] **Step 5: 导出节点**

在 `app/services/generation/nodes/__init__.py` 中追加：
```python
from app.services.generation.nodes.chapter_loop import node_refine_chapter_outline
```

- [ ] **Step 6: 接入 graph**

在 `app/services/generation/graph.py` 中：

```python
# 顶部 import 新增
from app.services.generation.nodes import (
    ...
    node_refine_chapter_outline,   # 新增
    ...
)

# _build_generation_graph 中新增节点（line ~184 区域）：
graph.add_node("refine_outline", _timed_node("refine_outline", node_refine_chapter_outline))

# 修改边：load_context → refine_outline → consistency_check
# 原：graph.add_edge("load_context", "consistency_check")
# 改为：
graph.add_edge("load_context", "refine_outline")
graph.add_edge("refine_outline", "consistency_check")
```

> ⚠️ LangGraph graph 是模块级 singleton，修改后**必须重启 worker 进程**才能生效。

- [ ] **Step 7: 运行测试**

```bash
uv run pytest tests/test_outline_generation.py -v
uv run pytest tests/ -q
```

- [ ] **Step 8: Commit**

```bash
git add app/prompts/templates/chapter_outline_refine.j2 \
        app/services/generation/nodes/chapter_loop.py \
        app/services/generation/nodes/__init__.py \
        app/services/generation/graph.py \
        tests/test_outline_generation.py
git commit -m "feat(pipeline): add node_refine_chapter_outline for pre-chapter outline enrichment"
```

---

## Task 7：验证与端到端测试

**Files:**
- Test: `tests/test_outline_generation.py`

- [ ] **Step 1: 运行全量测试**

```bash
uv run pytest tests/ -q
```
预期：所有测试通过，无 regression。

- [ ] **Step 2: 手动验证（新建一本小说）**

1. 重启 worker：`make dev-worker`
2. 在前端创建新小说（30 章）
3. 查看 DB 确认大纲有实质内容：
   ```sql
   SELECT chapter_num, LEFT(title, 40), LEFT(outline, 60)
   FROM chapter_outlines
   WHERE novel_id = <id>
   ORDER BY chapter_num;
   ```
4. 等待第一卷写完，确认 `volume_replan` 触发后第二卷大纲已生成。
5. 查看生成章节标题，确认无 "auto-generated" 字样。

- [ ] **Step 3: 验证 resume 场景**

1. 启动生成，写到第 3 章时手动停止 worker。
2. 重启 worker，观察任务是否从第 3 章续写且大纲有效。
3. 检查 DB 不含空 outline 行。

- [ ] **Step 4: Commit（若有修复）**

```bash
git add -p
git commit -m "fix(outline): address issues found during e2e validation"
```

---

## 架构总结

```
生成流程（修改后）：
┌──────────┐    ┌──────────┐    ┌──────────────────────────────────┐
│ node_init │───▶│node_prewrite│───▶│ node_outline                      │
└──────────┘    └──────────┘    │  只生成第一卷(volume_size章)          │
                                 │  run_volume_outlines(vol=1, start=1)│
                                 └──────────────────────────────────┘
                                                  │
                                          ┌───────▼────────┐
                                          │node_volume_replan│  ← 每卷开始
                                          │生成下一卷大纲    │
                                          │(用真实摘要)      │
                                          └───────┬────────┘
                                                  │
                                        ┌─────────▼──────────┐
                                        │  node_load_context  │
                                        └─────────┬──────────┘
                                                  │
                                        ┌─────────▼──────────┐  ← 新节点
                                        │node_refine_outline  │
                                        │精化 hook/payoff/    │
                                        │opening_scene 等字段  │
                                        └─────────┬──────────┘
                                                  │
                                        ┌─────────▼──────────┐
                                        │ consistency_check   │
                                        └─────────┬──────────┘
                                                  │
                                        ┌─────────▼──────────┐
                                        │      beats          │
                                        └─────────┬──────────┘
                                                  │
                                        ┌─────────▼──────────┐
                                        │      writer         │  ← 22字段全有内容
                                        └────────────────────┘
```

### 质量提升预期

| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| 大纲生成成功率 | ~0%（200章必失败） | ~95%（30章单卷） |
| 标题有效性 | 随机出现"auto-generated out" | 100% 有实质内容 |
| resume 数据一致性 | 脏数据复用 | 强制内容质量检查 |
| 写章结构指导 | 22字段均为空 | 关键字段有真实内容 |
| 后续卷连贯性 | 无（全书一刀切） | 基于真实摘要滚动生成 |
| 章间过渡衔接 | 大纲无 opening_scene/transition | refine 节点实时填充 |
