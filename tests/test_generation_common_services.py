from types import SimpleNamespace

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.novel import ChapterOutline, Novel, NovelSpecification
from app.services.generation.common import (
    generate_chapter_summary,
    save_full_outlines,
    save_prewrite_artifacts,
    update_character_states_from_content,
)


def _create_novel(title: str = "common-test") -> Novel:
    db = SessionLocal()
    try:
        novel = Novel(title=title, target_language="zh")
        db.add(novel)
        db.commit()
        db.refresh(novel)
        return novel
    finally:
        db.close()


def test_save_prewrite_artifacts_upsert():
    novel = _create_novel("prewrite-upsert")
    save_prewrite_artifacts(novel.id, {"architecture": {"a": 1}, "world": {"w": "x"}})
    save_prewrite_artifacts(novel.id, {"architecture": {"a": 2}})

    db = SessionLocal()
    try:
        rows = db.execute(
            select(NovelSpecification).where(NovelSpecification.novel_id == novel.id)
        ).scalars().all()
        assert len(rows) == 2
        by_type = {r.spec_type: r.content for r in rows}
        assert by_type["architecture"]["a"] == 2
        assert by_type["world"]["w"] == "x"
    finally:
        db.close()


def test_save_full_outlines_replace_and_metadata():
    novel = _create_novel("outline-replace")
    save_full_outlines(
        novel.id,
        [
            {"chapter_num": 1, "title": "一", "outline": "A", "purpose": "铺垫"},
            {"chapter_num": 2, "title": "二", "outline": "B", "purpose": "冲突"},
        ],
    )
    save_full_outlines(
        novel.id,
        [
            {"chapter_num": 3, "title": "三", "outline": "C", "purpose": "收束", "summary": "终章"},
        ],
    )

    db = SessionLocal()
    try:
        rows = db.execute(
            select(ChapterOutline).where(ChapterOutline.novel_id == novel.id).order_by(ChapterOutline.chapter_num.asc())
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].chapter_num == 3
        assert rows[0].metadata_["purpose"] == "收束"
        assert rows[0].metadata_["summary"] == "终章"
    finally:
        db.close()


def test_generate_chapter_summary_success(monkeypatch):
    monkeypatch.setattr("app.core.strategy.get_model_for_stage", lambda *_: ("openai", "mock"))

    class _LLM:
        def invoke(self, _prompt):
            return SimpleNamespace(content=" 这是摘要结果 ")

    monkeypatch.setattr("app.core.llm.get_llm_with_fallback", lambda *_: _LLM())
    out = generate_chapter_summary("正文" * 100, {"summary": "旧摘要"}, 6, "zh", "web-novel")
    assert out == "这是摘要结果"


def test_generate_chapter_summary_fallback_to_outline(monkeypatch):
    monkeypatch.setattr("app.core.strategy.get_model_for_stage", lambda *_: ("openai", "mock"))

    class _FailLLM:
        def invoke(self, _prompt):
            raise RuntimeError("llm down")

    monkeypatch.setattr("app.core.llm.get_llm_with_fallback", lambda *_: _FailLLM())
    out = generate_chapter_summary("正文", {"summary": "已有摘要"}, 3, "zh", "web-novel")
    assert out == "已有摘要"


def test_update_character_states_from_content_updates(monkeypatch):
    monkeypatch.setattr("app.core.strategy.get_model_for_stage", lambda *_: ("openai", "mock"))

    class _LLM:
        def invoke(self, _prompt):
            return SimpleNamespace(content='{"updates":[{"name":"林秋","state":"受伤"}]}')

    monkeypatch.setattr("app.core.llm.get_llm_with_fallback", lambda *_: _LLM())
    monkeypatch.setattr("app.services.generation.agents._parse_json_response", lambda s: {"updates": [{"name": "林秋", "state": "受伤"}]})

    class _Mgr:
        def __init__(self):
            self.calls = []

        def update_state(self, novel_id, name, payload, db=None):
            self.calls.append((novel_id, name, payload))

    mgr = _Mgr()
    prewrite = {"specification": {"characters": [{"name": "林秋"}]}}
    update_character_states_from_content(
        novel_id=1001,
        chapter_num=7,
        content="林秋在战斗后受伤。",
        prewrite=prewrite,
        char_mgr=mgr,  # type: ignore[arg-type]
        language="zh",
        strategy="web-novel",
    )
    assert len(mgr.calls) == 1
    assert mgr.calls[0][1] == "林秋"
    assert mgr.calls[0][2]["chapter_num"] == 7


def test_update_character_states_from_content_no_character_short_circuit(monkeypatch):
    called = {"v": False}

    class _LLM:
        def invoke(self, _prompt):
            called["v"] = True
            return SimpleNamespace(content="{}")

    monkeypatch.setattr("app.core.strategy.get_model_for_stage", lambda *_: ("openai", "mock"))
    monkeypatch.setattr("app.core.llm.get_llm_with_fallback", lambda *_: _LLM())
    prewrite = {"specification": {"characters": []}}
    update_character_states_from_content(
        novel_id=1002,
        chapter_num=1,
        content="无角色",
        prewrite=prewrite,
        char_mgr=SimpleNamespace(update_state=lambda *args, **kwargs: None),  # type: ignore[arg-type]
        language="zh",
        strategy="web-novel",
    )
    assert called["v"] is False
