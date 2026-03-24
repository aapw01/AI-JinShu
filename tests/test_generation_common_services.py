import logging
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.novel import ChapterOutline, Novel, NovelSpecification, NovelVersion
from app.services.generation.agents import WriterAgent
from app.services.generation.common import (
    generate_chapter_summary,
    save_full_outlines,
    save_prewrite_artifacts,
    upsert_chapter_outline,
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


def _create_version(novel: Novel, version_no: int = 1) -> NovelVersion:
    db = SessionLocal()
    try:
        version = NovelVersion(
            novel_id=novel.id,
            version_no=version_no,
            status="completed",
            is_default=1 if version_no == 1 else 0,
        )
        db.add(version)
        db.commit()
        db.refresh(version)
        return version
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


def test_upsert_chapter_outline_updates_same_version_chapter():
    novel = _create_novel("outline-upsert")
    version = _create_version(novel)

    upsert_chapter_outline(
        novel.id,
        {"chapter_num": 16, "title": "桥接前", "outline": "旧提纲", "purpose": "补铺垫"},
        novel_version_id=version.id,
    )
    normalized = upsert_chapter_outline(
        novel.id,
        {
            "chapter_num": 16,
            "title": "第16章 桥接补完",
            "outline": "新提纲",
            "purpose": "收束主线",
            "summary": "进入收官",
        },
        novel_version_id=version.id,
    )

    db = SessionLocal()
    try:
        rows = db.execute(
            select(ChapterOutline)
            .where(
                ChapterOutline.novel_id == novel.id,
                ChapterOutline.novel_version_id == version.id,
            )
            .order_by(ChapterOutline.chapter_num.asc())
        ).scalars().all()
        assert len(rows) == 1
        assert rows[0].chapter_num == 16
        assert rows[0].title == "第16章 桥接补完"
        assert rows[0].outline == "新提纲"
        assert rows[0].metadata_["purpose"] == "收束主线"
        assert rows[0].metadata_["summary"] == "进入收官"
        assert normalized["title"] == "第16章 桥接补完"
    finally:
        db.close()


def test_upsert_chapter_outline_persists_progression_contract_fields():
    novel = _create_novel("outline-contract")
    version = _create_version(novel)

    normalized = upsert_chapter_outline(
        novel.id,
        {
            "chapter_num": 8,
            "title": "第8章 真相逼近",
            "outline": "主角发现新的证据，并决定夜探旧宅。",
            "purpose": "推进主线",
            "chapter_objective": "发现幕后黑手的关键证据",
            "required_new_information": ["旧宅密室里藏有父亲留下的账册"],
            "required_irreversible_change": "主角决定主动入局",
            "relationship_delta": "主角开始怀疑盟友",
            "conflict_axis": "调查",
            "payoff_kind": "investigation",
            "reveal_kind": "truth",
            "forbidden_repeats": ["重复强调证据很重要但不行动"],
            "opening_scene": "深夜旧宅门口",
            "opening_character_positions": ["主角在门外", "盟友守在后门"],
            "opening_time_state": "当晚",
            "transition_mode": "aftermath",
        },
        novel_version_id=version.id,
    )

    db = SessionLocal()
    try:
        row = db.execute(
            select(ChapterOutline).where(
                ChapterOutline.novel_id == novel.id,
                ChapterOutline.novel_version_id == version.id,
                ChapterOutline.chapter_num == 8,
            )
        ).scalar_one()
        assert row.metadata_["chapter_objective"] == "发现幕后黑手的关键证据"
        assert row.metadata_["transition_mode"] == "aftermath"
        assert row.metadata_["opening_scene"] == "深夜旧宅门口"
        assert row.metadata_["forbidden_repeats"] == ["重复强调证据很重要但不行动"]
        assert normalized["required_new_information"] == ["旧宅密室里藏有父亲留下的账册"]
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


def test_update_character_states_from_content_only_passes_relevant_characters(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_render_prompt(_template, **kwargs):
        captured["character_names"] = kwargs["character_names"]
        return "prompt"

    monkeypatch.setattr("app.core.strategy.get_model_for_stage", lambda *_: ("openai", "mock"))
    monkeypatch.setattr("app.services.generation.common.render_prompt", _fake_render_prompt)

    class _LLM:
        def invoke(self, _prompt):
            return SimpleNamespace(content='{"updates":[{"name":"林秋","status":"injured"}]}')

    monkeypatch.setattr("app.core.llm.get_llm_with_fallback", lambda *_: _LLM())

    class _Mgr:
        def update_state(self, *args, **kwargs):
            return None

    prewrite = {
        "specification": {
            "characters": [{"name": "林秋"}, {"name": "陆沉"}, {"name": "顾北"}, {"name": "周姨"}]
        }
    }
    update_character_states_from_content(
        novel_id=1003,
        chapter_num=8,
        content="林秋在庭院中受了伤，但仍坚持追查真相。",
        prewrite=prewrite,
        char_mgr=_Mgr(),  # type: ignore[arg-type]
        language="zh",
        strategy="web-novel",
    )
    assert captured["character_names"] == "林秋"


def test_update_character_states_from_content_warns_on_invalid_payload(monkeypatch, caplog):
    monkeypatch.setattr("app.core.strategy.get_model_for_stage", lambda *_: ("openai", "mock"))

    class _LLM:
        def invoke(self, _prompt):
            return SimpleNamespace(content="not-json")

    monkeypatch.setattr("app.core.llm.get_llm_with_fallback", lambda *_: _LLM())
    prewrite = {"specification": {"characters": [{"name": "林秋"}]}}
    with caplog.at_level(logging.WARNING):
        update_character_states_from_content(
            novel_id=1004,
            chapter_num=9,
            content="林秋站在门外，神色紧绷。",
            prewrite=prewrite,
            char_mgr=SimpleNamespace(update_state=lambda *args, **kwargs: None),  # type: ignore[arg-type]
            language="zh",
            strategy="web-novel",
        )
    assert "returned no updates" in caplog.text or "invalid" in caplog.text


def test_writer_agent_error_includes_provider_model_and_root_cause(monkeypatch):
    def _raise(*_args, **_kwargs):
        raise RuntimeError("upstream timeout")

    monkeypatch.setattr("app.services.generation.agents.invoke_chapter_body_structured", _raise)

    writer = WriterAgent()
    with pytest.raises(RuntimeError) as exc_info:
        writer.run(
            novel_id="n-1",
            chapter_num=1,
            outline={"title": "开篇"},
            context={"scene": "测试"},
            language="zh",
            native_style_profile="",
            provider="openai",
            model="gpt-4o-mini",
        )

    msg = str(exc_info.value)
    assert "writer generation failed for chapter=1" in msg
    assert "provider=openai" in msg
    assert "model=gpt-4o-mini" in msg
    assert "cause=RuntimeError: upstream timeout" in msg
