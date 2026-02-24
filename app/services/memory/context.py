"""Layered context builder - assembles context within a token budget.

Layers (priority order):
1. Global Bible     - Novel specs, worldview, character roster (always included, compressed)
2. Thread Ledger    - Active foreshadowing/unresolved conflicts for current chapter
3. Recent Window   - Last 3-5 chapter summaries + last chapter's ending paragraph
4. Volume Brief     - Compressed summary of current volume (chapters grouped by ~30)
5. Knowledge Chunks - Vector store results filtered by chapter outline relevance
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.tokens import estimate_tokens
from app.models.novel import Chapter, NovelSpecification
from app.services.memory.summary_manager import SummaryManager
from app.services.memory.story_bible import StoryBibleStore
from app.services.memory.thread_ledger import get_thread_ledger
from app.services.memory.vector_store import VectorStoreWrapper

CONTEXT_TOKEN_BUDGET = 8000
RECENT_WINDOW_SIZE = 5
LAST_CHAPTER_ENDING_CHARS = 500
GLOBAL_BIBLE_MAX_CHARS = 3000
THREAD_LEDGER_MAX_CHARS = 1200
RECENT_WINDOW_MAX_CHARS = 2000
VOLUME_BRIEF_MAX_CHARS = 1600
STORY_BIBLE_MAX_CHARS = 2200


def _build_story_bible_context(novel_id: int, chapter_num: int, db: Session) -> str:
    bible = StoryBibleStore()
    entities = bible.list_entities(novel_id, db=db)
    events = bible.list_recent_events(novel_id, chapter_num - 1, limit=20, db=db)
    if not entities and not events:
        return ""
    char_entities = [e for e in entities if e.entity_type == "character"][:10]
    item_entities = [e for e in entities if e.entity_type == "item"][:8]
    lines: list[str] = []
    if char_entities:
        lines.append(
            "角色状态: "
            + "; ".join(
                f"{e.name}({e.status or 'unknown'})" for e in char_entities if e.name
            )
        )
    if item_entities:
        lines.append(
            "关键道具: "
            + "; ".join(
                f"{e.name}({e.summary or '已出现'})" for e in item_entities if e.name
            )
        )
    if events:
        lines.append(
            "近期事件: "
            + "; ".join(
                f"ch{ev.chapter_num}:{(ev.title or ev.event_type or '事件')}" for ev in events[:10]
            )
        )
    return "\n".join(lines)[:STORY_BIBLE_MAX_CHARS]

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
    stmt = select(NovelSpecification).where(NovelSpecification.novel_id == novel_id)
    rows = db.execute(stmt).scalars().all()
    return {r.spec_type: r.content for r in rows}


def _load_outline_from_db(novel_id: int, chapter_num: int, db: Session) -> dict:
    from app.models.novel import ChapterOutline

    stmt = select(ChapterOutline).where(
        ChapterOutline.novel_id == novel_id,
        ChapterOutline.chapter_num == chapter_num,
    )
    row = db.execute(stmt).scalar_one_or_none()
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
    stmt = select(Chapter).where(
        Chapter.novel_id == novel_id,
        Chapter.chapter_num == chapter_num - 1,
    )
    row = db.execute(stmt).scalar_one_or_none()
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
        story_bible_context = _build_story_bible_context(novel_id, chapter_num, db)

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
            estimate_tokens(global_bible)
            + estimate_tokens(thread_ledger_str)
            + estimate_tokens(recent_window)
            + estimate_tokens(volume_brief)
            + estimate_tokens(story_bible_context)
        )

        knowledge_chunks: list[dict] = []
        if used < token_budget:
            outline_text = f"{outline.get('title', '')}\n{outline.get('outline', '')}".strip()
            query_text = "\n".join(x for x in [outline_text, thread_ledger_str, recent_window] if x).strip()
            chunks = vector_store.search(novel_id, query_text=query_text, limit=5, db=db)
            for c in chunks:
                content = c.get("content", "") or str(c)
                if used + estimate_tokens(content) <= token_budget:
                    knowledge_chunks.append(c)
                    used += estimate_tokens(content)
                else:
                    break

        return {
            "global_bible": global_bible,
            "thread_ledger": thread_ledger,
            "thread_ledger_text": thread_ledger_str,
            "story_bible_context": story_bible_context,
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
