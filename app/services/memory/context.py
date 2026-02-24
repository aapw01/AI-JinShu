"""Layered context builder - assembles context within a token budget.

Layers (priority order):
1. Global Bible     - Novel specs, worldview, character roster (always included, compressed)
2. Thread Ledger    - Active foreshadowing/unresolved conflicts for current chapter
3. Recent Window   - Last 3-5 chapter summaries + last chapter's ending paragraph
4. Volume Brief     - Compressed summary of current volume (chapters grouped by ~30)
5. Knowledge Chunks - Vector store results filtered by chapter outline relevance
"""
from typing import Optional

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.novel import Chapter, NovelSpecification
from app.services.memory.summary_manager import SummaryManager
from app.services.memory.thread_ledger import get_thread_ledger
from app.services.memory.vector_store import VectorStoreWrapper

CONTEXT_TOKEN_BUDGET = 8000
RECENT_WINDOW_SIZE = 5
LAST_CHAPTER_ENDING_CHARS = 500
GLOBAL_BIBLE_MAX_CHARS = 3000
THREAD_LEDGER_MAX_CHARS = 1200
RECENT_WINDOW_MAX_CHARS = 2000
VOLUME_BRIEF_MAX_CHARS = 1600


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _compress_global_bible(prewrite: dict) -> str:
    parts = []
    constitution = prewrite.get("constitution") or {}
    if isinstance(constitution, dict):
        parts.append(f"宪法: {constitution.get('core_principles', constitution.get('principles', ''))[:800]}")
    elif constitution:
        parts.append(f"宪法: {str(constitution)[:800]}")

    spec = prewrite.get("specification") or prewrite.get("spec") or {}
    if isinstance(spec, dict):
        chars = spec.get("characters") or spec.get("character_roster") or []
        if isinstance(chars, list):
            char_brief = "; ".join(
                c.get("name", "") + ": " + str(c.get("role", c.get("description", "")))[:80]
                for c in chars[:15] if isinstance(c, dict)
            )[:600]
        else:
            char_brief = str(chars)[:600]
        parts.append(f"角色: {char_brief}")

        world = spec.get("worldview") or spec.get("world") or spec.get("setting") or ""
        if world:
            parts.append(f"世界观: {str(world)[:600]}")
    return "\n".join(parts)[:GLOBAL_BIBLE_MAX_CHARS]


def _load_prewrite_from_db(novel_id: int, db: Session) -> dict:
    rows = (
        db.query(NovelSpecification)
        .filter(NovelSpecification.novel_id == novel_id)
        .all()
    )
    return {r.spec_type: r.content for r in rows}


def _load_outline_from_db(novel_id: int, chapter_num: int, db: Session) -> dict:
    from app.models.novel import ChapterOutline

    row = (
        db.query(ChapterOutline)
        .filter(
            ChapterOutline.novel_id == novel_id,
            ChapterOutline.chapter_num == chapter_num,
        )
        .first()
    )
    if not row:
        return {"chapter_num": chapter_num, "title": f"第{chapter_num}章", "outline": ""}
    meta = row.metadata_ or {}
    return {
        "chapter_num": row.chapter_num,
        "title": row.title,
        "outline": row.outline or "",
        **meta,
    }


def _get_last_chapter_ending(novel_id: int, chapter_num: int, db: Session) -> str:
    if chapter_num <= 1:
        return ""
    row = (
        db.query(Chapter)
        .filter(
            Chapter.novel_id == novel_id,
            Chapter.chapter_num == chapter_num - 1,
        )
        .first()
    )
    if not row or not row.content:
        return ""
    return row.content[-LAST_CHAPTER_ENDING_CHARS:]


def _format_thread_ledger(ledger: dict) -> str:
    parts = []
    if ledger.get("active_foreshadowing"):
        items = ledger["active_foreshadowing"][:10]
        parts.append("待收伏笔: " + "; ".join(f"ch{i['chapter_num']}: {i['foreshadowing']}" for i in items))
    if ledger.get("active_plotlines"):
        parts.append("主线: " + "; ".join(ledger["active_plotlines"][:5]))
    if ledger.get("unresolved_hooks"):
        items = ledger["unresolved_hooks"][:5]
        parts.append("未解钩子: " + "; ".join(f"ch{i['chapter_num']}: {i['hook']}" for i in items))
    return "\n".join(parts)[:THREAD_LEDGER_MAX_CHARS]


def build_chapter_context(
    novel_id: int,
    chapter_num: int,
    prewrite: dict,
    outline: dict,
    db: Optional[Session] = None,
    token_budget: int = CONTEXT_TOKEN_BUDGET,
) -> dict:
    """Build layered context within token budget."""
    should_close = db is None
    db = db or SessionLocal()
    summary_mgr = SummaryManager()
    vector_store = VectorStoreWrapper()

    try:
        global_bible = _compress_global_bible(prewrite)
        thread_ledger = get_thread_ledger(novel_id, chapter_num, prewrite, db=db)
        thread_ledger_str = _format_thread_ledger(thread_ledger)

        all_before = summary_mgr.get_summaries_before(novel_id, chapter_num, db=db)
        recent_summaries = all_before[-RECENT_WINDOW_SIZE:]
        last_ending = _get_last_chapter_ending(novel_id, chapter_num, db)
        recent_parts = [f"第{s['chapter_num']}章: {s['summary']}" for s in recent_summaries]
        if last_ending:
            recent_parts.append(f"上章结尾: {last_ending}")
        recent_window = "\n".join(recent_parts)[:RECENT_WINDOW_MAX_CHARS]

        volume_brief = ""
        if chapter_num > RECENT_WINDOW_SIZE + 1:
            volume_brief = summary_mgr.get_volume_brief(
                novel_id, chapter_num, volume_size=30, db=db
            )[:VOLUME_BRIEF_MAX_CHARS]

        used = (
            _estimate_tokens(global_bible)
            + _estimate_tokens(thread_ledger_str)
            + _estimate_tokens(recent_window)
            + _estimate_tokens(volume_brief)
        )

        knowledge_chunks: list[dict] = []
        if used < token_budget:
            chunks = vector_store.search(novel_id, limit=5, db=db)
            for c in chunks:
                content = c.get("content", "") or str(c)
                if used + _estimate_tokens(content) <= token_budget:
                    knowledge_chunks.append(c)
                    used += _estimate_tokens(content)
                else:
                    break

        return {
            "global_bible": global_bible,
            "thread_ledger": thread_ledger,
            "thread_ledger_text": thread_ledger_str,
            "recent_window": recent_window,
            "volume_brief": volume_brief,
            "knowledge_chunks": knowledge_chunks,
            "budget_used": used,
            "budget_total": token_budget,
        }
    finally:
        if should_close:
            db.close()


def get_context_for_chapter(
    novel_id: int | str,
    chapter_num: int,
    db: Optional[Session] = None,
    prewrite: Optional[dict] = None,
    outline: Optional[dict] = None,
) -> dict:
    """Load relevant context for chapter generation. Backward compatible."""
    novel_id = int(novel_id)
    should_close = db is None
    db = db or SessionLocal()
    summary_mgr = SummaryManager()
    try:
        prewrite = prewrite if prewrite is not None else _load_prewrite_from_db(novel_id, db)
        outline = outline if outline is not None else _load_outline_from_db(novel_id, chapter_num, db)
        ctx = build_chapter_context(novel_id, chapter_num, prewrite, outline, db=db)
        all_before = summary_mgr.get_summaries_before(novel_id, chapter_num, db=db)
        ctx["summaries"] = all_before[-RECENT_WINDOW_SIZE:]
        from app.services.memory.character_state import CharacterStateManager

        char_mgr = CharacterStateManager()
        ctx["character_states"] = char_mgr.get_states(novel_id, chapter_num, db=db)
        return ctx
    finally:
        if should_close:
            db.close()
