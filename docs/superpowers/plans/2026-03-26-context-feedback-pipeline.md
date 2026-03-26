# Context Feedback Pipeline 完整实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复"数据提取了但 writer 完全看不见"的根本问题，并补全角色关系图谱，让 writer 每章能看到动态境界/伤势/情绪、具体事实、人物关系变化。

**Architecture:** 分三阶段。Phase 1 修补本次已完成的基础接线（字段不对齐、规范化遗漏）。Phase 2 用零新表成本实现轻量级关系摘要（NovelMemory）。Phase 3 建正式 story_relations 表，将关系数据从键值升级为可查询的结构化图。

**Tech Stack:** Python / FastAPI / SQLAlchemy 2.0 / Alembic / Pydantic v2 / Jinja2 / pytest

---

## 文件变更总览

| 文件 | 操作 | 负责内容 |
|------|------|---------|
| `app/services/generation/agents.py` | 修改 | CharacterStateUpdateSchema 加 realm/emotional_state；新增 CharacterRelationSchema + run_relation_extraction() |
| `app/prompts/templates/character_state_updates_from_chapter.j2` | 修改 | 模板 JSON schema 加 realm/emotional_state |
| `app/prompts/templates/character_relation_extraction.j2` | 新建 | 提取章节内关系变化 |
| `app/services/generation/heuristics.py` | 修改 | normalize_progression_payload 加 foreshadow_check |
| `app/services/generation/nodes/finalize.py` | 修改 | 调用 run_relation_extraction，存入 NovelMemory |
| `app/services/memory/context.py` | 修改 | 读取 NovelMemory 关系数据注入 writer |
| `app/models/novel.py` | 修改 | 新增 StoryRelation 模型 |
| `alembic/versions/<hash>_add_story_relations.py` | 新建 | story_relations 表迁移 |
| `app/services/memory/story_bible.py` | 修改 | upsert_relation() + list_relations() |
| `tests/test_context_feedback.py` | 新建 | Phase 1+2 集成测试 |
| `tests/test_story_relations.py` | 新建 | Phase 3 关系图谱测试 |

---

## Phase 1：修补已有接线的字段不对齐（Items 1-4 硬化）

### Task 1：CharacterStateUpdateSchema 补全提取字段

**背景：** context.py 读取 `realm`、`power_level`、`cultivation`、`emotional_state` 等字段，但这些字段既不在 `CharacterStateUpdateSchema` 里，也不在提取模板的 JSON schema 里。`novel_memory` 里存的数据缺这些字段，导致 Fix 1 接线完成但注入内容为空。

**Files:**
- Modify: `app/services/generation/agents.py:580-591`
- Modify: `app/prompts/templates/character_state_updates_from_chapter.j2`
- Test: `tests/test_context_feedback.py`

- [ ] **Step 1：写失败测试**

```python
# tests/test_context_feedback.py
from app.services.generation.agents import CharacterStateUpdateSchema, CharacterStateUpdatesSchema


def test_character_state_schema_has_realm_and_emotion():
    """Schema must accept realm and emotional_state from LLM output."""
    raw = {
        "name": "林舟",
        "status": "alive",
        "location": "青云城",
        "realm": "金丹期",
        "emotional_state": "愤怒",
        "injuries": ["左臂骨折"],
        "new_items": [],
        "lost_items": [],
        "can_use_both_hands": False,
        "limitations": [],
        "forbidden_actions": [],
        "relationship_changes": [],
        "key_action": "突破金丹",
    }
    update = CharacterStateUpdateSchema(**raw)
    assert update.realm == "金丹期"
    assert update.emotional_state == "愤怒"


def test_character_state_updates_schema_roundtrip():
    payload = {"updates": [{"name": "林舟", "realm": "化神期", "emotional_state": "平静"}]}
    schema = CharacterStateUpdatesSchema(**payload)
    assert schema.updates[0].realm == "化神期"
    assert schema.updates[0].emotional_state == "平静"
```

- [ ] **Step 2：运行确认失败**

```bash
uv run pytest tests/test_context_feedback.py::test_character_state_schema_has_realm_and_emotion -v
```
预期：`FAILED` — `CharacterStateUpdateSchema` 没有 `realm` 字段

- [ ] **Step 3：更新 CharacterStateUpdateSchema**

在 `app/services/generation/agents.py` 的 `CharacterStateUpdateSchema` class（约 580 行）末尾加两个字段：

```python
class CharacterStateUpdateSchema(BaseModel):
    name: str = ""
    status: str = "unknown"
    location: str = ""
    new_items: list[str] = Field(default_factory=list)
    lost_items: list[str] = Field(default_factory=list)
    injuries: list[str] = Field(default_factory=list)
    can_use_both_hands: bool = True
    limitations: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    relationship_changes: list[str] = Field(default_factory=list)
    key_action: str = ""
    realm: str = ""
    emotional_state: str = ""
```

- [ ] **Step 4：更新提取模板**

将 `app/prompts/templates/character_state_updates_from_chapter.j2` 第 6 行 JSON schema 替换为：

```
Output JSON: {"updates": [{"name": "角色名", "status": "alive/injured/dead/unknown", "location": "当前位置", "realm": "修为/境界/等级（若本章有变化则填写，否则留空）", "emotional_state": "当前情绪状态（如：愤怒/悲伤/平静/决然，否则留空）", "new_items": [], "lost_items": [], "injuries": [], "can_use_both_hands": true, "limitations": [], "forbidden_actions": [], "relationship_changes": [], "key_action": "本章关键行为"}]}
Only include characters from the provided list who actually appeared or were directly affected. If none of them changed state, return {"updates": []}. Output pure JSON.
```

- [ ] **Step 5：运行测试确认通过**

```bash
uv run pytest tests/test_context_feedback.py -v
```
预期：`2 passed`

- [ ] **Step 6：提交**

```bash
git add app/services/generation/agents.py \
        app/prompts/templates/character_state_updates_from_chapter.j2 \
        tests/test_context_feedback.py
git commit -m "fix(memory): add realm/emotional_state to CharacterStateUpdateSchema and extraction template"
```

---

### Task 2：normalize_progression_payload 透传 foreshadow_check

**背景：** `reviewer_combined.j2` 现在会输出 `foreshadow_check` 字段，但 `normalize_progression_payload`（heuristics.py）只提取固定字段列表，不含 `foreshadow_check`，导致 `progression_pack` 里没有这个字段，下游无法访问。

**Files:**
- Modify: `app/services/generation/heuristics.py:185-198`
- Test: `tests/test_context_feedback.py`

- [ ] **Step 1：写失败测试**

```python
# 追加到 tests/test_context_feedback.py

from app.services.generation.heuristics import normalize_progression_payload


def test_normalize_progression_payload_carries_foreshadow_check():
    """foreshadow_check from reviewer must survive normalization."""
    raw = {
        "score": 0.75,
        "confidence": 0.7,
        "feedback": "有推进",
        "duplicate_beats": [],
        "no_new_delta": [],
        "repeated_reveal": [],
        "repeated_relationship_turn": [],
        "transition_conflict": [],
        "foreshadow_check": ["FS-0003", "FS-0007"],
    }
    result = normalize_progression_payload(raw, "推进审校")
    assert result["foreshadow_check"] == ["FS-0003", "FS-0007"]


def test_normalize_progression_payload_missing_foreshadow_check_defaults_empty():
    """Old reviewer output without foreshadow_check must not crash."""
    raw = {"score": 0.8, "confidence": 0.7, "feedback": "ok"}
    result = normalize_progression_payload(raw, "推进审校")
    assert result["foreshadow_check"] == []
```

- [ ] **Step 2：运行确认失败**

```bash
uv run pytest tests/test_context_feedback.py::test_normalize_progression_payload_carries_foreshadow_check -v
```
预期：`FAILED` — `KeyError: 'foreshadow_check'`

- [ ] **Step 3：修改 normalize_progression_payload**

在 `app/services/generation/heuristics.py` 的 `normalize_progression_payload` 中，将字段列表替换：

```python
def normalize_progression_payload(result: Any, default_feedback: str = "") -> dict[str, Any]:
    payload = normalize_reviewer_payload(result, default_feedback)
    raw = payload.get("raw")
    if not isinstance(raw, dict):
        raw = result if isinstance(result, dict) else {}
    for field in [
        "duplicate_beats",
        "no_new_delta",
        "repeated_reveal",
        "repeated_relationship_turn",
        "transition_conflict",
    ]:
        payload[field] = [str(x)[:180] for x in (raw.get(field) or []) if str(x).strip()][:8]
    payload["foreshadow_check"] = [str(x)[:64] for x in (raw.get("foreshadow_check") or []) if str(x).strip()][:5]
    return payload
```

- [ ] **Step 4：运行测试**

```bash
uv run pytest tests/test_context_feedback.py -v
```
预期：`4 passed`

- [ ] **Step 5：提交**

```bash
git add app/services/generation/heuristics.py tests/test_context_feedback.py
git commit -m "fix(review): add foreshadow_check passthrough in normalize_progression_payload"
```

---

## Phase 2：轻量级关系摘要（Item 5，零新表）

### Task 3：创建关系提取模板 + Schema + FactExtractor 方法

**背景：** `character_dynamics.j2` 是个分析存根，从未接入流水线。本 Task 新建专用的关系变化提取模板，并在 `FactExtractorAgent` 上挂一个 `run_relation_extraction()` 方法，为 Task 4 的接线做准备。每章只提取"本章发生了哪些关系变化"，不做全量图谱，成本低。

**Files:**
- Create: `app/prompts/templates/character_relation_extraction.j2`
- Modify: `app/services/generation/agents.py` — 新增 Schema + 方法
- Test: `tests/test_context_feedback.py`

- [ ] **Step 1：创建模板文件**

```
# app/prompts/templates/character_relation_extraction.j2
分析第{{ chapter_num }}章正文，提取**本章发生变化**的角色关系（建立/破裂/加深/疏远/对立/和解）。

已知角色列表：{{ character_names }}

正文（截断至3000字）：
{{ content }}

仅输出本章实际发生关系变化的条目，没有则输出空列表。每条最多 3 条。

输出 JSON：
{
  "relations": [
    {
      "source": "角色A",
      "target": "角色B",
      "relation_type": "师徒/仇敌/盟友/恋人/家人/竞争/陌生/其他",
      "description": "关系描述（20字以内）",
      "change": "建立/加深/破裂/疏远/对立/和解",
      "evidence": "正文原句（15字以内）"
    }
  ]
}
```

- [ ] **Step 2：写失败测试**

```python
# 追加到 tests/test_context_feedback.py

from app.services.generation.agents import CharacterRelationSchema, CharacterRelationsSchema


def test_character_relation_schema_basic():
    raw = {
        "source": "林舟",
        "target": "苏晴",
        "relation_type": "盟友",
        "description": "共同对抗魔门",
        "change": "建立",
        "evidence": "林舟握住苏晴的手",
    }
    rel = CharacterRelationSchema(**raw)
    assert rel.source == "林舟"
    assert rel.change == "建立"


def test_character_relations_schema_empty():
    schema = CharacterRelationsSchema(relations=[])
    assert schema.relations == []
```

- [ ] **Step 3：运行确认失败**

```bash
uv run pytest tests/test_context_feedback.py::test_character_relation_schema_basic -v
```
预期：`ImportError: cannot import name 'CharacterRelationSchema'`

- [ ] **Step 4：在 agents.py 添加 Schema（紧接 CharacterStateUpdatesSchema 之后，约 596 行）**

```python
class CharacterRelationSchema(BaseModel):
    source: str = ""
    target: str = ""
    relation_type: str = ""
    description: str = ""
    change: str = ""
    evidence: str = ""


class CharacterRelationsSchema(BaseModel):
    relations: list[CharacterRelationSchema] = Field(default_factory=list)
```

- [ ] **Step 5：在 FactExtractorAgent 末尾添加 run_relation_extraction 方法**

找到 `FactExtractorAgent` 类（约 1930 行），在 `run_foreshadow_extraction` 方法之后追加：

```python
def run_relation_extraction(
    self,
    chapter_num: int,
    content: str,
    character_names: list[str],
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    """Extract character relationship changes for this chapter."""
    if not character_names or not content:
        return {"relations": []}
    try:
        prompt = render_prompt(
            "character_relation_extraction",
            chapter_num=chapter_num,
            character_names=", ".join(character_names[:20]),
            content=content[:3000],
        )
        llm = get_llm_with_fallback(provider, model)
        result = _invoke_json_with_schema(
            llm,
            prompt,
            CharacterRelationsSchema,
            strict=False,
            stage="relation_extraction",
            provider=provider,
            model=model,
            chapter_num=chapter_num,
            prompt_template="character_relation_extraction",
            prompt_version="v1",
        )
        return result if isinstance(result, dict) else {"relations": []}
    except Exception as exc:
        logger.warning("run_relation_extraction failed chapter=%s error=%s", chapter_num, exc)
        return {"relations": []}
```

- [ ] **Step 6：运行测试**

```bash
uv run pytest tests/test_context_feedback.py -v
```
预期：`6 passed`

- [ ] **Step 7：提交**

```bash
git add app/prompts/templates/character_relation_extraction.j2 \
        app/services/generation/agents.py \
        tests/test_context_feedback.py
git commit -m "feat(memory): add character relation extraction schema and FactExtractor method"
```

---

### Task 4：接线关系提取到 finalize + context 注入

**Files:**
- Modify: `app/services/generation/nodes/finalize.py` — 调用 run_relation_extraction
- Modify: `app/services/memory/context.py` — 读取关系数据注入

- [ ] **Step 1：写失败测试（验证 context 包含关系字段）**

```python
# 追加到 tests/test_context_feedback.py
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session


def test_build_story_bible_context_includes_relations(client):
    """When novel_memory has character_relation entries, context includes 角色关系."""
    from app.core.database import SessionLocal, resolve_novel
    from app.models.novel import NovelMemory
    from app.services.memory.context import _build_story_bible_context

    r = client.post("/api/novels", json={"title": "RelTest", "target_language": "zh"})
    assert r.status_code == 200
    novel_public_id = r.json()["id"]

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_public_id)
        novel_id = int(novel.id)
        # Seed a relation record in NovelMemory
        rel_record = NovelMemory(
            novel_id=novel_id,
            novel_version_id=None,
            memory_type="character_relation",
            key="rel_林舟__苏晴",
            content={
                "source": "林舟",
                "target": "苏晴",
                "relation_type": "盟友",
                "description": "共同对抗魔门",
                "updated_chapter": 5,
            },
        )
        db.add(rel_record)
        db.commit()

        result = _build_story_bible_context(novel_id, None, 6, db)
        assert "角色关系" in result
        assert "林舟" in result
        assert "苏晴" in result
    finally:
        db.close()
```

- [ ] **Step 2：运行确认失败**

```bash
uv run pytest tests/test_context_feedback.py::test_build_story_bible_context_includes_relations -v
```
预期：`FAILED` — `assert '角色关系' in result`

- [ ] **Step 3：在 finalize.py 调用 run_relation_extraction**

在 `finalize.py` 中，找到 `update_character_states_from_content` 调用块（约 339-357 行）之后，追加：

```python
        # --- 关系提取（轻量，存 NovelMemory）---
        try:
            _fe_rel_provider, _fe_rel_model = get_model_for_stage(state["strategy"], "fact_extractor")
            char_names_for_rel = [
                c.get("name", "") for c in
                ((state.get("prewrite") or {}).get("specification") or {}).get("characters") or []
                if isinstance(c, dict) and c.get("name")
            ][:20]
            if char_names_for_rel:
                rel_result = state["fact_extractor"].run_relation_extraction(
                    chapter_num=chapter_num,
                    content=final_content,
                    character_names=char_names_for_rel,
                    provider=_fe_rel_provider,
                    model=_fe_rel_model,
                )
                from app.models.novel import NovelMemory as _NM
                from app.core.database import SessionLocal as _SL
                _rel_db = db or _SL()
                _rel_close = db is None
                try:
                    for rel in (rel_result or {}).get("relations") or []:
                        if not (rel.get("source") and rel.get("target")):
                            continue
                        key = f"rel_{rel['source']}__{rel['target']}"
                        existing = _rel_db.execute(
                            select(_NM).where(
                                _NM.novel_id == state["novel_id"],
                                _NM.memory_type == "character_relation",
                                _NM.key == key,
                            )
                        ).scalar_one_or_none()
                        content_payload = {**rel, "updated_chapter": chapter_num}
                        if existing:
                            existing.content = content_payload
                        else:
                            _rel_db.add(_NM(
                                novel_id=state["novel_id"],
                                novel_version_id=state.get("novel_version_id"),
                                memory_type="character_relation",
                                key=key,
                                content=content_payload,
                            ))
                    if _rel_close:
                        _rel_db.commit()
                    else:
                        _rel_db.flush()
                finally:
                    if _rel_close:
                        _rel_db.close()
        except Exception as _rel_exc:
            logger.warning("relation_extraction failed chapter=%s error=%s", chapter_num, _rel_exc)
```

同时在文件顶部 import 区确认有 `from sqlalchemy import select`（已有）。

- [ ] **Step 4：在 context.py 的 `_build_story_bible_context` 末尾、items 块之前注入关系数据**

在 `_build_story_bible_context` 中 `if item_entities:` 行之前加：

```python
    # Relations from novel_memory (stored by run_relation_extraction in finalize)
    try:
        rel_stmt = select(NovelMemory).where(
            NovelMemory.novel_id == novel_id,
            NovelMemory.memory_type == "character_relation",
        )
        if novel_version_id is not None:
            rel_stmt = rel_stmt.where(NovelMemory.novel_version_id == novel_version_id)
        rel_rows = db.execute(rel_stmt).scalars().all()
        if rel_rows:
            # Priority: relations involving outline characters first
            rel_rows_sorted = sorted(
                rel_rows,
                key=lambda r: (
                    0 if outline_chars and (
                        any(c in (r.content or {}).get("source", "") for c in outline_chars)
                        or any(c in (r.content or {}).get("target", "") for c in outline_chars)
                    ) else 1
                ),
            )
            rel_lines: list[str] = []
            for r in rel_rows_sorted[:10]:
                c = r.content if isinstance(r.content, dict) else {}
                src, tgt, rtype, desc = (
                    c.get("source", ""),
                    c.get("target", ""),
                    c.get("relation_type", ""),
                    c.get("description", ""),
                )
                ch = int(c.get("updated_chapter") or 0)
                if src and tgt:
                    ch_tag = f"[至ch{ch}]" if ch else ""
                    rel_lines.append(f"{src}→{tgt}({rtype}){ch_tag}: {desc[:30]}")
            if rel_lines:
                lines.append("角色关系: " + "; ".join(rel_lines))
    except Exception as _exc:
        _log.warning("story_bible_context: relation read failed: %s", _exc)
```

- [ ] **Step 5：运行全部测试**

```bash
uv run pytest tests/test_context_feedback.py -v
```
预期：`7 passed`

- [ ] **Step 6：运行现有测试确认无回归**

```bash
uv run pytest -q tests/ 2>&1 | tail -5
```
预期：无新增失败

- [ ] **Step 7：提交**

```bash
git add app/services/generation/nodes/finalize.py \
        app/services/memory/context.py \
        tests/test_context_feedback.py
git commit -m "feat(memory): wire character relation extraction in finalize and inject into writer context"
```

---

## Phase 3：正式关系图谱（Item 6，story_relations 表）

### Task 5：新增 StoryRelation 模型 + Alembic 迁移

**背景：** Phase 2 用 `NovelMemory` 做键值存储，无法按"角色对"高效查询、无法做时序关系变迁分析。本 Task 建专用表，为后续查询/分析奠基。

**Files:**
- Modify: `app/models/novel.py`
- Create: `alembic/versions/<hash>_add_story_relations.py`
- Test: `tests/test_story_relations.py`

- [ ] **Step 1：写失败测试**

```python
# tests/test_story_relations.py
"""Tests for story_relations table and StoryBibleStore relation methods."""
from app.core.database import SessionLocal, resolve_novel
from app.services.memory.story_bible import StoryBibleStore


def test_upsert_and_list_relations(client):
    r = client.post("/api/novels", json={"title": "RelGraph Test", "target_language": "zh"})
    assert r.status_code == 200
    novel_public_id = r.json()["id"]

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_public_id)
        novel_id = int(novel.id)
        store = StoryBibleStore()

        # Upsert a relation
        rel = store.upsert_relation(
            novel_id=novel_id,
            source_name="林舟",
            target_name="苏晴",
            relation_type="盟友",
            description="共同对抗魔门",
            since_chapter=3,
            updated_chapter=5,
            db=db,
        )
        assert rel.id is not None
        assert rel.source_name == "林舟"
        assert rel.relation_type == "盟友"

        # Upsert same pair updates description
        rel2 = store.upsert_relation(
            novel_id=novel_id,
            source_name="林舟",
            target_name="苏晴",
            relation_type="盟友",
            description="并肩作战，情谊加深",
            since_chapter=3,
            updated_chapter=8,
            db=db,
        )
        assert rel2.id == rel.id  # same row updated
        assert rel2.description == "并肩作战，情谊加深"
        assert rel2.updated_chapter == 8

        # List relations
        relations = store.list_relations(novel_id=novel_id, db=db)
        assert len(relations) == 1
        assert relations[0].source_name == "林舟"
    finally:
        db.close()


def test_list_relations_by_chapter(client):
    r = client.post("/api/novels", json={"title": "RelChapter Test", "target_language": "zh"})
    assert r.status_code == 200
    novel_public_id = r.json()["id"]

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_public_id)
        novel_id = int(novel.id)
        store = StoryBibleStore()

        store.upsert_relation(novel_id=novel_id, source_name="A", target_name="B",
                              relation_type="仇敌", description="", since_chapter=1, updated_chapter=10, db=db)
        store.upsert_relation(novel_id=novel_id, source_name="C", target_name="D",
                              relation_type="师徒", description="", since_chapter=5, updated_chapter=5, db=db)

        # Only relations updated on or before ch6
        rels = store.list_relations(novel_id=novel_id, updated_before_chapter=7, db=db)
        assert any(r.source_name == "C" for r in rels)
    finally:
        db.close()
```

- [ ] **Step 2：运行确认失败**

```bash
uv run pytest tests/test_story_relations.py -v
```
预期：`FAILED` — `StoryBibleStore has no attribute 'upsert_relation'`

- [ ] **Step 3：在 app/models/novel.py 末尾（Index 块之前）加 StoryRelation 模型**

找到文件末尾的 `Index(...)` 块之前，插入：

```python
class StoryRelation(Base):
    """Persistent character relationship record, upserted after each chapter."""

    __tablename__ = "story_relations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True)
    source_name = Column(String(255), nullable=False)
    target_name = Column(String(255), nullable=False)
    relation_type = Column(String(100), nullable=False, default="unknown")
    description = Column(Text, nullable=True)
    since_chapter = Column(Integer, nullable=False, default=1)
    updated_chapter = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)
```

在 Index 块末尾追加：

```python
Index(
    "idx_story_relations_novel_src_tgt_type",
    StoryRelation.novel_version_id,
    StoryRelation.source_name,
    StoryRelation.target_name,
    StoryRelation.relation_type,
    unique=True,
)
Index("idx_story_relations_novel_updated", StoryRelation.novel_version_id, StoryRelation.updated_chapter)
```

- [ ] **Step 4：生成 Alembic 迁移**

```bash
uv run alembic revision --autogenerate -m "add_story_relations"
```

检查生成的迁移文件（`alembic/versions/*_add_story_relations.py`），确认 `upgrade()` 含 `story_relations` 表创建，`downgrade()` 含 `drop_table`。

- [ ] **Step 5：应用迁移**

```bash
uv run alembic upgrade head
```
预期：`Running upgrade ... -> <hash>, add_story_relations`

- [ ] **Step 6：运行测试确认表已创建**

```bash
uv run pytest tests/test_story_relations.py -v
```
预期：`FAILED` 但不是 table not found —— 而是 `StoryBibleStore has no attribute 'upsert_relation'`（进度正确）

- [ ] **Step 7：提交**

```bash
git add app/models/novel.py alembic/versions/
git commit -m "feat(db): add story_relations table for character relationship graph"
```

---

### Task 6：StoryBibleStore 关系 CRUD 方法

**Files:**
- Modify: `app/services/memory/story_bible.py`
- Test: `tests/test_story_relations.py`

- [ ] **Step 1：在 story_bible.py 顶部导入 StoryRelation**

```python
from app.models.novel import (
    StoryEntity,
    StoryFact,
    StoryEvent,
    StoryForeshadow,
    StoryRelation,    # 新增
    StorySnapshot,
    GenerationCheckpoint,
    QualityReport,
    NovelMemory,
)
```

- [ ] **Step 2：在 StoryBibleStore 的 list_recent_events 方法之后添加两个方法**

```python
def upsert_relation(
    self,
    novel_id: int,
    source_name: str,
    target_name: str,
    relation_type: str,
    description: str | None,
    since_chapter: int,
    updated_chapter: int,
    novel_version_id: int | None = None,
    db: Optional[Session] = None,
) -> StoryRelation:
    """Insert or update a character relationship record."""
    should_close = db is None
    db = db or SessionLocal()
    try:
        stmt = select(StoryRelation).where(
            StoryRelation.novel_id == novel_id,
            StoryRelation.source_name == source_name,
            StoryRelation.target_name == target_name,
            StoryRelation.relation_type == relation_type,
        )
        if novel_version_id is not None:
            stmt = stmt.where(StoryRelation.novel_version_id == novel_version_id)
        row = db.execute(stmt).scalar_one_or_none()
        if row:
            row.description = description if description is not None else row.description
            row.updated_chapter = updated_chapter
        else:
            try:
                with db.begin_nested():
                    row = StoryRelation(
                        novel_id=novel_id,
                        novel_version_id=novel_version_id,
                        source_name=source_name,
                        target_name=target_name,
                        relation_type=relation_type,
                        description=description,
                        since_chapter=since_chapter,
                        updated_chapter=updated_chapter,
                    )
                    db.add(row)
                    db.flush()
            except IntegrityError:
                row = db.execute(stmt).scalar_one_or_none()
                if row is None:
                    raise
                row.description = description if description is not None else row.description
                row.updated_chapter = updated_chapter
        if should_close:
            db.commit()
        else:
            db.flush()
        db.refresh(row)
        return row
    except Exception:
        if should_close:
            db.rollback()
        raise
    finally:
        if should_close:
            db.close()

def list_relations(
    self,
    novel_id: int,
    novel_version_id: int | None = None,
    updated_before_chapter: int | None = None,
    limit: int = 40,
    db: Optional[Session] = None,
) -> list[StoryRelation]:
    """Return all (or recent) character relations for a novel."""
    should_close = db is None
    db = db or SessionLocal()
    try:
        stmt = select(StoryRelation).where(StoryRelation.novel_id == novel_id)
        if novel_version_id is not None:
            stmt = stmt.where(StoryRelation.novel_version_id == novel_version_id)
        if updated_before_chapter is not None:
            stmt = stmt.where(StoryRelation.updated_chapter < updated_before_chapter)
        stmt = stmt.order_by(StoryRelation.updated_chapter.desc()).limit(max(1, limit))
        return db.execute(stmt).scalars().all()
    finally:
        if should_close:
            db.close()
```

- [ ] **Step 3：运行测试**

```bash
uv run pytest tests/test_story_relations.py -v
```
预期：`2 passed`

- [ ] **Step 4：提交**

```bash
git add app/services/memory/story_bible.py tests/test_story_relations.py
git commit -m "feat(memory): add upsert_relation and list_relations to StoryBibleStore"
```

---

### Task 7：将关系存储从 NovelMemory 升级到 story_relations + 更新 context 注入

**Files:**
- Modify: `app/services/generation/nodes/finalize.py` — 替换 NovelMemory 写入为 upsert_relation
- Modify: `app/services/memory/context.py` — 替换 NovelMemory 查询为 list_relations
- Test: `tests/test_story_relations.py`

- [ ] **Step 1：写集成测试（验证 context 读取 story_relations）**

```python
# 追加到 tests/test_story_relations.py

def test_build_story_bible_context_reads_story_relations(client):
    """context._build_story_bible_context must include 角色关系 from story_relations."""
    from app.core.database import SessionLocal, resolve_novel
    from app.services.memory.context import _build_story_bible_context
    from app.services.memory.story_bible import StoryBibleStore

    r = client.post("/api/novels", json={"title": "CtxRelTest", "target_language": "zh"})
    assert r.status_code == 200
    novel_public_id = r.json()["id"]

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_public_id)
        novel_id = int(novel.id)
        store = StoryBibleStore()
        store.upsert_relation(
            novel_id=novel_id,
            source_name="陈平",
            target_name="叶凡",
            relation_type="仇敌",
            description="陈平杀害了叶凡的师父",
            since_chapter=2,
            updated_chapter=12,
            db=db,
        )
        db.commit()

        ctx = _build_story_bible_context(novel_id, None, 13, db)
        assert "角色关系" in ctx
        assert "陈平" in ctx
        assert "叶凡" in ctx
    finally:
        db.close()
```

- [ ] **Step 2：运行确认失败**

```bash
uv run pytest tests/test_story_relations.py::test_build_story_bible_context_reads_story_relations -v
```
预期：`FAILED` — context 仍从 NovelMemory 读（story_relations 为空）

- [ ] **Step 3：更新 finalize.py — 关系写入改用 upsert_relation**

找到 Task 4 Step 3 添加的关系提取块，将 NovelMemory 写入部分替换为：

```python
                # 写入 story_relations（正式图谱）
                _bible_store = state.get("bible_store")
                if _bible_store is None:
                    from app.services.memory.story_bible import StoryBibleStore as _SBS
                    _bible_store = _SBS()
                for rel in (rel_result or {}).get("relations") or []:
                    if not (rel.get("source") and rel.get("target")):
                        continue
                    try:
                        _bible_store.upsert_relation(
                            novel_id=state["novel_id"],
                            novel_version_id=state.get("novel_version_id"),
                            source_name=str(rel["source"])[:255],
                            target_name=str(rel["target"])[:255],
                            relation_type=str(rel.get("relation_type") or "unknown")[:100],
                            description=str(rel.get("description") or "")[:500],
                            since_chapter=chapter_num,
                            updated_chapter=chapter_num,
                            db=db,
                        )
                    except Exception as _upsert_exc:
                        logger.warning("upsert_relation failed chapter=%s pair=%s->%s error=%s",
                                       chapter_num, rel.get("source"), rel.get("target"), _upsert_exc)
```

同时删除或注释掉 Task 4 写入 NovelMemory 的代码块（避免重复写）。

- [ ] **Step 4：更新 context.py — 关系读取改用 list_relations**

在 `_build_story_bible_context` 中，将 Task 4 添加的 NovelMemory 关系读取块替换为：

```python
    # Relations from story_relations table (upserted by run_relation_extraction in finalize)
    try:
        relations = StoryBibleStore().list_relations(
            novel_id=novel_id,
            novel_version_id=novel_version_id,
            updated_before_chapter=chapter_num,
            limit=20,
            db=db,
        )
        if relations:
            if outline_chars:
                relations = sorted(
                    relations,
                    key=lambda r: (
                        0 if any(c in r.source_name or c in r.target_name for c in outline_chars) else 1,
                        -r.updated_chapter,
                    ),
                )
            rel_lines = []
            for r in relations[:12]:
                ch_tag = f"[至ch{r.updated_chapter}]" if r.updated_chapter else ""
                desc = (r.description or "")[:30]
                rel_lines.append(f"{r.source_name}→{r.target_name}({r.relation_type}){ch_tag}: {desc}")
            if rel_lines:
                lines.append("角色关系: " + "; ".join(rel_lines))
    except Exception as _exc:
        _log.warning("story_bible_context: relation read failed: %s", _exc)
```

（同时删除 Task 4 中添加的 NovelMemory 关系查询块）

- [ ] **Step 5：运行所有关系测试**

```bash
uv run pytest tests/test_story_relations.py -v
```
预期：`3 passed`

- [ ] **Step 6：运行全量测试确认无回归**

```bash
uv run pytest -q tests/ 2>&1 | tail -5
```
预期：无新增失败（数量与 Phase 1 前相同或更多 passed）

- [ ] **Step 7：提交**

```bash
git add app/services/generation/nodes/finalize.py \
        app/services/memory/context.py \
        tests/test_story_relations.py
git commit -m "feat(memory): upgrade character relation storage to story_relations table, inject into writer context"
```

---

## 验收标准

| 检查项 | 验证方式 |
|--------|---------|
| CharacterState realm/emotion 被提取 | 跑一次真实生成，查 `SELECT content FROM novel_memory WHERE memory_type='character'`，确认有 realm 字段 |
| foreshadow_check 字段出现在 reviewer 输出 | 查 quality_reports 表 metrics_json 里 progression 维度含 foreshadow_check |
| writer context 包含角色动态 | 在 INFO 日志搜索 `story_bible_context`（或加临时 log），确认输出含"角色动态"块 |
| 角色关系从第 3 章起出现在 context | 查 story_relations 表有记录，且 `_build_story_bible_context` 返回含"角色关系"块 |
| 所有测试通过 | `uv run pytest -q tests/` 无新增失败 |
