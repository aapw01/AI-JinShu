from __future__ import annotations

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.novel import Novel, NovelMemory
from app.services.memory.progression_state import (
    ProgressionMemoryManager,
    build_anti_repeat_constraints,
    build_transition_constraints,
)
from app.services.memory.context import build_chapter_context


def _create_novel(title: str = "progression-state-test") -> Novel:
    db = SessionLocal()
    try:
        novel = Novel(title=title, target_language="zh")
        db.add(novel)
        db.commit()
        db.refresh(novel)
        return novel
    finally:
        db.close()


def test_list_recent_advancements_returns_sorted_window_before_chapter():
    novel = _create_novel("recent-advancements-window")
    db = SessionLocal()
    try:
        for chapter_num in range(1, 9):
            db.add(
                NovelMemory(
                    novel_id=novel.id,
                    memory_type="chapter_advancement",
                    key=str(chapter_num),
                    content={
                        "chapter_num": chapter_num,
                        "chapter_objective": f"推进{chapter_num}",
                    },
                )
            )
        db.commit()
    finally:
        db.close()

    mgr = ProgressionMemoryManager()
    rows = mgr.list_recent_advancements(novel.id, before_chapter=7, limit=3)

    assert [item["chapter_num"] for item in rows] == [4, 5, 6]


def test_build_constraint_helpers_surface_recent_and_transition_state():
    anti_repeat = build_anti_repeat_constraints(
        recent_advancements=[
            {
                "chapter_objective": "揭示身世",
                "new_information": ["主角是云家嫡女"],
                "relationship_delta": "主角开始怀疑盟友",
                "forbidden_repeats": ["不要重复用身世揭晓当本章主推进"],
            }
        ],
        volume_arc_state={
            "payoff_kinds": ["truth_reveal"],
            "forbidden_repeats": ["卷内已经打过一次认亲牌"],
        },
        book_progression_state={
            "major_beats": ["认亲"],
            "revealed_information": ["主角是云家嫡女"],
        },
        outline_contract={"chapter_objective": "推进主线调查"},
    )
    transition = build_transition_constraints(
        {
            "ending_scene": "别墅门外",
            "last_action": "主角摔门而出",
            "time_state": "当晚",
            "scene_exit": "冲出别墅大门",
        }
    )

    assert anti_repeat["current_objective"] == "推进主线调查"
    assert anti_repeat["recent_objectives"] == ["揭示身世"]
    assert "主角是云家嫡女" in anti_repeat["book_revealed_information"]
    assert transition["previous_transition_state"]["ending_scene"] == "别墅门外"
    assert any("缺少过渡" in item or "显式交代过渡" in item for item in transition["opening_constraints"])


def test_save_chapter_advancement_upserts_same_key():
    novel = _create_novel("progression-upsert")
    mgr = ProgressionMemoryManager()

    mgr.save_chapter_advancement(
        novel.id,
        3,
        {"chapter_objective": "旧目标"},
    )
    mgr.save_chapter_advancement(
        novel.id,
        3,
        {"chapter_objective": "新目标"},
    )

    db = SessionLocal()
    try:
        rows = db.execute(
            select(NovelMemory).where(
                NovelMemory.novel_id == novel.id,
                NovelMemory.memory_type == "chapter_advancement",
                NovelMemory.key == "3",
            )
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].content["chapter_objective"] == "新目标"
    finally:
        db.close()


def test_build_chapter_context_includes_context_sources(monkeypatch):
    novel = _create_novel("context-sources")
    db = SessionLocal()
    try:
        db.add(
            NovelMemory(
                novel_id=novel.id,
                memory_type="chapter_advancement",
                key="1",
                content={"chapter_num": 1, "chapter_objective": "揭示真相"},
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "app.services.memory.context._build_story_bible_context",
        lambda *_args, **_kwargs: "角色状态: 林初(active)",
    )
    monkeypatch.setattr(
        "app.services.memory.context.get_thread_ledger",
        lambda *_args, **_kwargs: {"active_plotlines": ["云家主线"]},
    )
    monkeypatch.setattr(
        "app.services.memory.context._get_last_chapter_ending",
        lambda *_args, **_kwargs: "林初推门离开。",
    )

    class _SummaryMgr:
        def get_summaries_before(self, *_args, **_kwargs):
            return [{"chapter_num": 1, "summary": "林初确认云家线索。"}]

        def get_volume_brief(self, *_args, **_kwargs):
            return "第一卷概览"

    class _VectorStore:
        def search(self, *_args, **_kwargs):
            return [{"content": "知识块", "chunk_type": "memory"}]

    monkeypatch.setattr("app.services.memory.context.SummaryManager", lambda: _SummaryMgr())
    monkeypatch.setattr("app.services.memory.context.VectorStoreWrapper", lambda: _VectorStore())

    ctx = build_chapter_context(
        novel.id,
        novel_version_id=None,
        chapter_num=2,
        prewrite={"specification": {"characters": [{"name": "林初", "role": "主角"}]}},
        outline={"chapter_num": 2, "title": "第2章", "outline": "推进云家线"},
    )

    assert "context_sources" in ctx
    assert any(item["source_type"] == "global_bible" for item in ctx["context_sources"])
    assert any(item["source_type"] == "recent_advancement_window" for item in ctx["context_sources"])
    assert any(item["source_type"] == "knowledge_chunks" for item in ctx["context_sources"])
    assert ctx["context_sources"][-1]["included"] in {True, False}
    assert all("value" not in item for item in ctx["context_sources"])
    assert all(set(item.keys()) == {"source_type", "source_key", "chapter_range", "selection_reason", "priority", "approx_tokens", "included"} for item in ctx["context_sources"])


def test_build_chapter_context_recent_window_preserves_immediate_predecessor(monkeypatch):
    novel = _create_novel("context-recent-window")

    monkeypatch.setattr(
        "app.services.memory.context._build_story_bible_context",
        lambda *_args, **_kwargs: "",
    )
    monkeypatch.setattr(
        "app.services.memory.context.get_thread_ledger",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.memory.context._get_last_chapter_ending",
        lambda *_args, **_kwargs: "",
    )

    class _SummaryMgr:
        def get_summaries_before(self, *_args, **_kwargs):
            return [
                {"chapter_num": 4, "summary": "旧线索继续推进。"},
                {"chapter_num": 5, "summary": "旧线索继续推进。"},
                {"chapter_num": 6, "summary": "旧线索继续推进。"},
                {"chapter_num": 7, "summary": "旧线索继续推进。"},
                {"chapter_num": 8, "summary": "上一章刚刚发生正面冲突。"},
            ]

        def get_volume_brief(self, *_args, **_kwargs):
            return ""

    class _VectorStore:
        def search(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr("app.services.memory.context.SummaryManager", lambda: _SummaryMgr())
    monkeypatch.setattr("app.services.memory.context.VectorStoreWrapper", lambda: _VectorStore())

    ctx = build_chapter_context(
        novel.id,
        novel_version_id=None,
        chapter_num=9,
        prewrite={"specification": {"characters": []}},
        outline={"chapter_num": 9, "title": "第9章", "outline": "本章延续上一章冲突"},
    )

    selected = ctx["summaries"]
    selected_chapters = [item["chapter_num"] for item in selected]

    assert 8 in selected_chapters
    assert ctx["constraint_usage_notes"]["selected_recent_chapters"] == selected_chapters
