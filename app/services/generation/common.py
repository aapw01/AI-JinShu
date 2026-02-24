"""Shared constants and helpers for generation pipeline modules."""
import logging
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.tokens import estimate_tokens
from app.models.novel import ChapterOutline, NovelSpecification
from app.services.memory.character_state import CharacterStateManager

logger = logging.getLogger("app.services.generation")

REVIEW_SCORE_THRESHOLD = 0.7
MAX_RETRIES = 2

def save_prewrite_artifacts(novel_id: int, prewrite: dict) -> None:
    db = SessionLocal()
    try:
        for spec_type, content in prewrite.items():
            stmt = select(NovelSpecification).where(
                NovelSpecification.novel_id == novel_id,
                NovelSpecification.spec_type == spec_type,
            )
            existing = db.execute(stmt).scalar_one_or_none()
            if existing:
                existing.content = content
            else:
                db.add(NovelSpecification(novel_id=novel_id, spec_type=spec_type, content=content))
        db.commit()
    finally:
        db.close()


def save_full_outlines(novel_id: int, outlines: list[dict]) -> None:
    db = SessionLocal()
    try:
        db.execute(delete(ChapterOutline).where(ChapterOutline.novel_id == novel_id))
        for o in outlines:
            db.add(
                ChapterOutline(
                    novel_id=novel_id,
                    chapter_num=o.get("chapter_num"),
                    title=o.get("title"),
                    outline=o.get("outline"),
                    metadata_={
                        "role": o.get("role"),
                        "purpose": o.get("purpose"),
                        "suspense_level": o.get("suspense_level"),
                        "foreshadowing": o.get("foreshadowing"),
                        "plot_twist_level": o.get("plot_twist_level"),
                        "hook": o.get("hook"),
                        "payoff": o.get("payoff"),
                        "mini_climax": o.get("mini_climax"),
                        "summary": o.get("summary"),
                    },
                )
            )
        db.commit()
    finally:
        db.close()


def generate_chapter_summary(
    content: str, outline: dict, chapter_num: int, language: str, strategy: str
) -> str:
    from app.core.llm import get_llm_with_fallback
    from app.core.strategy import get_model_for_stage

    provider, model = get_model_for_stage(strategy, "reviewer")
    llm = get_llm_with_fallback(provider, model)
    prompt = (
        f"Summarize this chapter in 200-400 characters. Include: key events, "
        f"character state changes, new information revealed, and the chapter-end hook.\n\n"
        f"Chapter {chapter_num} content (truncated):\n{content[:4000]}\n\n"
        f"Output only the summary text in {language}, no JSON or markdown."
    )
    try:
        resp = llm.invoke(prompt)
        return resp.content.strip()[:800]
    except Exception:
        return outline.get("summary") or f"第{chapter_num}章摘要"


def update_character_states_from_content(
    novel_id: int,
    chapter_num: int,
    content: str,
    prewrite: dict,
    char_mgr: CharacterStateManager,
    language: str,
    strategy: str,
    db=None,
) -> None:
    from app.core.llm import get_llm_with_fallback
    from app.core.strategy import get_model_for_stage
    from app.services.generation.agents import _parse_json_response

    provider, model = get_model_for_stage(strategy, "reviewer")
    llm = get_llm_with_fallback(provider, model)
    characters = prewrite.get("specification", {}).get("characters", [])
    if not isinstance(characters, list) or not characters:
        return
    char_names = [c.get("name", "") for c in characters if isinstance(c, dict) and c.get("name")]
    if not char_names:
        return
    prompt = (
        f"Based on this chapter content, report any STATE CHANGES for these characters: {', '.join(char_names)}\n\n"
        f"Chapter {chapter_num} content (truncated):\n{content[:3000]}\n\n"
        f'Output JSON: {{"updates": [{{"name": "角色名", "status": "alive/injured/dead/unknown", '
        f'"location": "当前位置", "new_items": [], "lost_items": [], '
        f'"relationship_changes": [], "key_action": "本章关键行为"}}]}}\n'
        f"Only include characters who actually appeared or were affected. Output pure JSON."
    )
    try:
        resp = llm.invoke(prompt)
        data = _parse_json_response(resp.content)
        for update in data.get("updates", []):
            if isinstance(update, dict) and update.get("name"):
                char_mgr.update_state(novel_id, update["name"], {"chapter_num": chapter_num, **update}, db=db)
    except Exception as e:
        logger.warning(f"Character state update from content failed: {e}")
