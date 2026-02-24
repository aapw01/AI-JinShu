"""Thread ledger - tracks active foreshadowing, unresolved conflicts, and plot threads."""
from typing import Optional

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.novel import ChapterOutline


def _ensure_list(val) -> list:
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x) for x in val if x]
    return [str(val)] if val else []


def get_thread_ledger(
    novel_id: int,
    chapter_num: int,
    prewrite: dict,
    db: Optional[Session] = None,
) -> dict:
    """Extract active foreshadowing, plotlines, and unresolved hooks for current chapter."""
    should_close = db is None
    db = db or SessionLocal()
    try:
        rows = (
            db.query(ChapterOutline)
            .filter(ChapterOutline.novel_id == novel_id)
            .order_by(ChapterOutline.chapter_num)
            .all()
        )

        active_foreshadowing = []
        for r in rows:
            meta = r.metadata_ or {}
            foreshadow = _ensure_list(meta.get("foreshadowing"))
            if foreshadow and r.chapter_num < chapter_num:
                for item in foreshadow:
                    active_foreshadowing.append({
                        "chapter_num": r.chapter_num,
                        "foreshadowing": item,
                        "title": r.title,
                    })

        unresolved_hooks = []
        for r in rows:
            if r.chapter_num >= chapter_num:
                continue
            meta = r.metadata_ or {}
            hooks = _ensure_list(meta.get("hook"))
            for h in hooks:
                unresolved_hooks.append({
                    "chapter_num": r.chapter_num,
                    "hook": h,
                    "title": r.title,
                })

        spec = prewrite.get("specification") or prewrite.get("spec") or {}
        plotlines = spec.get("plotlines") or spec.get("plot_lines") or []
        if isinstance(plotlines, list):
            active_plotlines = [p for p in plotlines if p] if plotlines else []
            if active_plotlines and isinstance(active_plotlines[0], dict):
                active_plotlines = [p.get("name") or p.get("description") or str(p) for p in active_plotlines]
            else:
                active_plotlines = [str(p) for p in active_plotlines]
        else:
            active_plotlines = [str(plotlines)] if plotlines else []

        return {
            "active_foreshadowing": active_foreshadowing,
            "active_plotlines": active_plotlines,
            "unresolved_hooks": unresolved_hooks,
        }
    finally:
        if should_close:
            db.close()
