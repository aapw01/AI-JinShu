from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.novel import Novel, StoryCharacterProfile
from app.services.generation.character_profiles import update_character_profiles_incremental


class _DummyResp:
    def __init__(self, content: str):
        self.content = content


class _DummyLLM:
    def invoke(self, _prompt: str):
        return _DummyResp("{}")


def test_incremental_character_profile_upsert_is_idempotent(monkeypatch):
    monkeypatch.setattr(
        "app.services.generation.character_profiles.get_llm_with_fallback",
        lambda *_args, **_kwargs: _DummyLLM(),
    )
    monkeypatch.setattr(
        "app.services.generation.character_profiles.get_model_for_stage",
        lambda *_args, **_kwargs: ("openai", "gpt-4o-mini"),
    )

    db = SessionLocal()
    try:
        novel = Novel(title="人物画像测试", target_language="zh", status="generating")
        db.add(novel)
        db.commit()
        db.refresh(novel)

        prewrite = {
            "specification": {
                "characters": [
                    {"name": "林深"},
                    {"name": " 林深 "},
                    {"name": "林-深"},
                ]
            }
        }
        update_character_profiles_incremental(
            db=db,
            novel_id=novel.id,
            chapter_num=1,
            content="林深走进雨里。",
            prewrite=prewrite,
            extracted_facts={},
            target_language="zh",
            strategy="web-novel",
        )
        db.commit()

        rows = db.execute(
            select(StoryCharacterProfile).where(StoryCharacterProfile.novel_id == novel.id)
        ).scalars().all()
        keys = [r.character_key for r in rows]
        assert keys.count("林深") == 1
    finally:
        db.close()

