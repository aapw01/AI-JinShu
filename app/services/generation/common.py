"""Shared constants and helpers for generation pipeline modules."""
import logging
import re
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models.novel import ChapterOutline, NovelSpecification
from app.prompts import render_prompt
from app.services.memory.character_state import CharacterStateManager

logger = logging.getLogger("app.services.generation")

REVIEW_SCORE_THRESHOLD = 0.7
MAX_RETRIES = 2
_PLACEHOLDER_TITLE_RE = re.compile(
    r"^\s*(第\s*[零一二三四五六七八九十百千两\d]+\s*章|chapter\s*\d+|ch\s*\d+)\s*$",
    re.IGNORECASE,
)


def normalize_title_text(title: str | None) -> str:
    """Normalize title text by trimming and collapsing repeated chapter prefixes."""
    text = str(title or "").strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(
        r"^(第\s*[零一二三四五六七八九十百千两\d]+\s*章)\s*\1+",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    return text[:80].strip()


def is_effective_title(title: str | None, chapter_num: int | None = None) -> bool:
    """Check if title is meaningful (not empty and not chapter-number placeholder)."""
    text = normalize_title_text(title)
    if not text:
        return False
    if _PLACEHOLDER_TITLE_RE.match(text):
        return False
    if chapter_num is not None:
        chapter_str = str(chapter_num)
        if text in {f"第{chapter_str}章", f"Chapter {chapter_str}", f"CH {chapter_str}", f"Ch {chapter_str}"}:
            return False
    return True


def _extract_title_suffix(text: str | None) -> str:
    source = str(text or "").strip()
    if not source:
        return ""
    source = source.replace("\r", "\n")
    source = re.sub(r"第\s*[零一二三四五六七八九十百千两\d]+\s*章[:：]?", "", source)
    parts = re.split(r"[。！？!?\n；;]", source)
    for part in parts:
        segment = part.strip()
        if not segment:
            continue
        segment = re.split(r"[，,、]", segment)[0].strip()
        segment = re.sub(r"^(本章|这一章|该章|章节)\s*", "", segment)
        if len(segment) < 2:
            continue
        if segment in {"推进主线", "推进剧情", "无", "none", "None"}:
            continue
        if len(segment) > 18:
            segment = segment[:18].rstrip("，,、:： ")
        if segment:
            return segment
    return ""


def resolve_chapter_title(
    chapter_num: int,
    title: str | None = None,
    outline: dict | None = None,
    summary: str | None = None,
    content: str | None = None,
) -> str:
    """Resolve a readable chapter title without overriding valid existing titles."""
    normalized = normalize_title_text(title)
    if is_effective_title(normalized, chapter_num):
        return normalized

    outline_obj = outline or {}
    candidates = [
        _extract_title_suffix(outline_obj.get("summary")),
        _extract_title_suffix(outline_obj.get("purpose")),
        _extract_title_suffix(outline_obj.get("outline")),
        _extract_title_suffix(summary),
        _extract_title_suffix(content),
    ]
    suffix = next((x for x in candidates if x), "")
    if suffix:
        return normalize_title_text(f"第{chapter_num}章：{suffix}")
    return f"第{chapter_num}章：关键事件"

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
                try:
                    with db.begin_nested():
                        db.add(NovelSpecification(novel_id=novel_id, spec_type=spec_type, content=content))
                        db.flush()
                except IntegrityError:
                    existing = db.execute(stmt).scalar_one_or_none()
                    if existing:
                        existing.content = content
                    else:
                        raise
        db.commit()
    finally:
        db.close()


def save_full_outlines(novel_id: int, outlines: list[dict], novel_version_id: int | None = None) -> None:
    db = SessionLocal()
    try:
        del_stmt = delete(ChapterOutline).where(ChapterOutline.novel_id == novel_id)
        if novel_version_id is not None:
            del_stmt = del_stmt.where(ChapterOutline.novel_version_id == novel_version_id)
        db.execute(del_stmt)
        for o in outlines:
            db.add(
                ChapterOutline(
                    novel_id=novel_id,
                    novel_version_id=novel_version_id,
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
    from app.core.llm import get_llm_with_fallback, response_to_text
    from app.core.strategy import get_model_for_stage

    provider, model = get_model_for_stage(strategy, "reviewer")
    llm = get_llm_with_fallback(provider, model)
    prompt = render_prompt(
        "chapter_summary_generate",
        chapter_num=chapter_num,
        content=(content[:4000]),
        language=language,
    )
    try:
        resp = llm.invoke(prompt)
        return response_to_text(resp).strip()[:800]
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
    novel_version_id: int | None = None,
) -> None:
    from app.core.llm import get_llm_with_fallback, response_to_text
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
    prompt = render_prompt(
        "character_state_updates_from_chapter",
        character_names=", ".join(char_names),
        chapter_num=chapter_num,
        content=(content[:3000]),
    )
    try:
        resp = llm.invoke(prompt)
        data = _parse_json_response(response_to_text(resp))
        for update in data.get("updates", []):
            if isinstance(update, dict) and update.get("name"):
                payload = {"chapter_num": chapter_num, **update}
                if novel_version_id is None:
                    char_mgr.update_state(novel_id, update["name"], payload, db=db)
                else:
                    try:
                        char_mgr.update_state(
                            novel_id,
                            update["name"],
                            payload,
                            db=db,
                            novel_version_id=novel_version_id,
                        )
                    except TypeError:
                        # Backward-compatible path for legacy managers in tests.
                        char_mgr.update_state(novel_id, update["name"], payload, db=db)
    except Exception as e:
        logger.warning(f"Character state update from content failed: {e}")
