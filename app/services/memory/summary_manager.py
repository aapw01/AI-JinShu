"""Summary manager - chapter summaries for context."""
from typing import Optional
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.novel import ChapterSummary


class SummaryManager:
    """Manage chapter summaries."""

    def get_summaries_before(
        self, novel_id: int, before_chapter: int, db: Optional[Session] = None
    ) -> list[dict]:
        """Get summaries of chapters before given number."""
        should_close = db is None
        db = db or SessionLocal()
        try:
            rows = (
                db.query(ChapterSummary)
                .filter(ChapterSummary.novel_id == novel_id, ChapterSummary.chapter_num < before_chapter)
                .order_by(ChapterSummary.chapter_num)
                .all()
            )
            return [{"chapter_num": r.chapter_num, "summary": r.summary} for r in rows]
        finally:
            if should_close:
                db.close()

    def get_volume_brief(
        self,
        novel_id: int,
        chapter_num: int,
        volume_size: int = 30,
        chars_per_volume: int = 400,
        db: Optional[Session] = None,
    ) -> str:
        """Compress older chapter summaries into volume-level briefs.

        For chapters older than (chapter_num - 5), group them into volumes of volume_size
        and return a compressed summary string.
        """
        cutoff = max(1, chapter_num - 5)
        should_close = db is None
        db = db or SessionLocal()
        try:
            rows = (
                db.query(ChapterSummary)
                .filter(
                    ChapterSummary.novel_id == novel_id,
                    ChapterSummary.chapter_num < cutoff,
                )
                .order_by(ChapterSummary.chapter_num)
                .all()
            )
            if not rows:
                return ""

            by_volume: dict[int, list[tuple[int, str]]] = {}
            for r in rows:
                vol_idx = (r.chapter_num - 1) // volume_size
                if vol_idx not in by_volume:
                    by_volume[vol_idx] = []
                by_volume[vol_idx].append((r.chapter_num, r.summary or ""))

            parts = []
            for vol_idx in sorted(by_volume.keys()):
                items = by_volume[vol_idx]
                start_ch, end_ch = items[0][0], items[-1][0]
                combined = " ".join(s for _, s in items)
                truncated = combined[:chars_per_volume] + ("..." if len(combined) > chars_per_volume else "")
                parts.append(f"【卷{vol_idx + 1} ({start_ch}-{end_ch})】{truncated}")
            return " ".join(parts)
        finally:
            if should_close:
                db.close()

    def add_summary(
        self, novel_id: int, chapter_num: int, summary: str, db: Optional[Session] = None
    ):
        """Add or update chapter summary."""
        should_close = db is None
        db = db or SessionLocal()
        try:
            existing = (
                db.query(ChapterSummary)
                .filter(ChapterSummary.novel_id == novel_id, ChapterSummary.chapter_num == chapter_num)
                .first()
            )
            if existing:
                existing.summary = summary
            else:
                db.add(ChapterSummary(novel_id=novel_id, chapter_num=chapter_num, summary=summary))
            db.commit()
        finally:
            if should_close:
                db.close()
