from __future__ import annotations

from app.core.database import SessionLocal
from app.models.novel import Novel, StoryCharacterProfile
from app.prompts import render_prompt
from app.services.memory.character_focus import build_character_focus_pack
from app.services.memory.context import build_chapter_context


def test_build_character_focus_pack_selects_outline_relevant_character():
    pack = build_character_focus_pack(
        prewrite={
            "specification": {
                "characters": [
                    {
                        "name": "林秋",
                        "role": "主角",
                        "goal": "查清旧案",
                        "motivation": "保护妹妹",
                        "voice": "克制、短句、少解释",
                    },
                    {"name": "陆沉", "role": "盟友", "goal": "隐藏身份"},
                ]
            }
        },
        outline={
            "outline": "林秋夜探旧宅，发现账册。",
            "opening_character_positions": ["林秋在旧宅门外"],
            "conflict_axis": "林秋必须在追兵到来前取走账册",
        },
        character_states=[
            {
                "key": "林秋",
                "content": {
                    "status": "injured",
                    "location": "旧宅门外",
                    "emotional_state": "警觉",
                    "limitations": ["左臂受伤"],
                },
            }
        ],
        profiles=[
            StoryCharacterProfile(
                novel_id=1,
                character_key="林秋",
                display_name="林秋",
                visual_do_not_change_json=["银色发夹"],
                signature_items_json=["旧账册钥匙"],
                confidence=0.9,
            )
        ],
    )

    assert pack["selected_character_keys"] == ["林秋"]
    item = pack["characters"][0]
    assert item["name"] == "林秋"
    assert item["motivation"] == "保护妹妹"
    assert item["voice"] == "克制、短句、少解释"
    assert item["current_state"]["location"] == "旧宅门外"
    assert "左臂受伤" in item["continuity_locks"]
    assert "银色发夹" in item["continuity_locks"]


def test_build_character_focus_pack_matches_role_and_alias_mentions():
    pack = build_character_focus_pack(
        prewrite={
            "specification": {
                "characters": [
                    {"name": "陆沉", "role": "盟友"},
                    {"name": "林秋", "role": "主角", "aliases": ["阿秋"], "motivation": "保护妹妹"},
                ]
            }
        },
        outline={
            "outline": "主角必须用阿秋这个旧称套出账册线索。",
            "conflict_axis": "主角不能暴露身份。",
        },
    )

    assert pack["selected_character_keys"] == ["林秋"]
    assert pack["characters"][0]["name"] == "林秋"


def test_next_chapter_prompt_renders_character_focus_pack():
    prompt = render_prompt(
        "next_chapter",
        chapter_num=4,
        outline={"title": "旧宅", "outline": "林秋夜探旧宅"},
        context={
            "global_bible": "世界设定",
            "recent_window": "上一章摘要",
            "thread_ledger_text": "线索A",
            "character_focus_pack": {
                "characters": [{"name": "林秋", "motivation": "保护妹妹"}],
                "selected_character_keys": ["林秋"],
            },
        },
        language="zh",
        native_style_profile="都市悬疑",
    )

    assert "<character_focus_pack_json>" in prompt
    assert '"motivation": "保护妹妹"' in prompt


def test_build_chapter_context_includes_character_focus_pack(monkeypatch):
    db = SessionLocal()
    try:
        novel = Novel(title="人物约束包测试", target_language="zh")
        db.add(novel)
        db.commit()
        db.refresh(novel)
        novel_id = novel.id
    finally:
        db.close()

    monkeypatch.setattr("app.services.memory.context._build_story_bible_context", lambda *_args, **_kwargs: "")
    monkeypatch.setattr("app.services.memory.context.get_thread_ledger", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("app.services.memory.context._get_last_chapter_ending", lambda *_args, **_kwargs: "")

    class _SummaryMgr:
        def get_summaries_before(self, *_args, **_kwargs):
            return []

        def get_volume_brief(self, *_args, **_kwargs):
            return ""

    class _VectorStore:
        def search(self, *_args, **_kwargs):
            return []

    monkeypatch.setattr("app.services.memory.context.SummaryManager", lambda: _SummaryMgr())
    monkeypatch.setattr("app.services.memory.context.VectorStoreWrapper", lambda: _VectorStore())

    ctx = build_chapter_context(
        novel_id,
        novel_version_id=None,
        chapter_num=2,
        prewrite={
            "specification": {
                "characters": [
                    {"name": "林秋", "role": "主角", "motivation": "保护妹妹"},
                    {"name": "陆沉", "role": "盟友"},
                ]
            }
        },
        outline={"chapter_num": 2, "title": "旧宅", "outline": "林秋夜探旧宅。"},
    )

    assert ctx["character_focus_pack"]["characters"][0]["name"] == "林秋"
    assert ctx["character_focus_pack"]["selected_character_keys"] == ["林秋"]
    assert any(item["source_type"] == "character_focus_pack" for item in ctx["context_sources"])
