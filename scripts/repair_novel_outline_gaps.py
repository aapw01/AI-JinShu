from __future__ import annotations

import argparse
import re

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.creation_task import CreationTask
from app.models.novel import ChapterOutline, ChapterVersion, GenerationTask, Novel, NovelVersion
from app.services.generation.common import upsert_chapter_outline

DEFAULT_TITLE = "人前不熟！人后备孕！夜夜被宠亲"


def _clean_excerpt(text: str | None, limit: int = 320) -> str:
    collapsed = re.sub(r"\s+", " ", str(text or "")).strip()
    return collapsed[:limit]


def _purpose_from_summary(summary: str | None) -> str:
    cleaned = str(summary or "").strip()
    if not cleaned:
        return "历史补写目录元数据"
    first = re.split(r"[。！？!?；;]", cleaned)[0].strip()
    return (first or cleaned)[:120]


def _latest_generation_task(db, novel_id: int) -> CreationTask | None:
    return db.execute(
        select(CreationTask)
        .where(
            CreationTask.resource_type == "novel",
            CreationTask.resource_id == novel_id,
            CreationTask.task_type == "generation",
        )
        .order_by(CreationTask.id.desc())
    ).scalar_one_or_none()


def _latest_legacy_generation_task(db, novel_id: int) -> GenerationTask | None:
    return db.execute(
        select(GenerationTask)
        .where(GenerationTask.novel_id == novel_id)
        .order_by(GenerationTask.id.desc())
    ).scalar_one_or_none()


def _repair_task_totals(task: CreationTask | None, legacy_task: GenerationTask | None, *, chapter_max: int) -> tuple[int, int, int]:
    current = int(chapter_max or 0)
    total = int(chapter_max or 0)
    completed = 0
    if task is not None:
        payload = task.payload_json if isinstance(task.payload_json, dict) else {}
        result = dict(task.result_json) if isinstance(task.result_json, dict) else {}
        cursor = task.resume_cursor_json if isinstance(task.resume_cursor_json, dict) else {}
        runtime_state = cursor.get("runtime_state") if isinstance(cursor.get("runtime_state"), dict) else {}
        start = int(result.get("start_chapter") or payload.get("start_chapter") or 1)
        last_completed = int(cursor.get("last_completed") or 0)
        runtime_end = int(runtime_state.get("effective_end_chapter") or 0)
        runtime_total = int(runtime_state.get("effective_total_chapters") or 0)
        current = max(current, runtime_end, last_completed)
        total = max(total, runtime_total, runtime_end, last_completed)
        completed = max(0, current - start + 1) if current >= start else 0
        result.update(
            {
                "start_chapter": start,
                "current_chapter": current,
                "total_chapters": total,
                "completed_chapters": completed,
            }
        )
        task.result_json = result
    if legacy_task is not None:
        legacy_task.current_chapter = current
        legacy_task.total_chapters = total
        if int(legacy_task.progress or 0) < 100 and str(legacy_task.status or "") == "completed":
            legacy_task.progress = 100
    return current, total, completed


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair missing chapter outlines for a completed novel task.")
    parser.add_argument("--title", default=DEFAULT_TITLE, help="Novel title to repair")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        novel = db.execute(
            select(Novel).where(Novel.title == args.title).order_by(Novel.id.desc())
        ).scalar_one_or_none()
        if novel is None:
            raise SystemExit(f"Novel not found: {args.title}")

        version = db.execute(
            select(NovelVersion)
            .where(NovelVersion.novel_id == novel.id, NovelVersion.is_default == 1)
            .order_by(NovelVersion.id.desc())
        ).scalar_one_or_none()
        if version is None:
            raise SystemExit(f"Default version not found for novel {novel.id}")

        chapters = db.execute(
            select(ChapterVersion)
            .where(ChapterVersion.novel_version_id == version.id)
            .order_by(ChapterVersion.chapter_num.asc())
        ).scalars().all()
        outlines = db.execute(
            select(ChapterOutline)
            .where(
                ChapterOutline.novel_id == novel.id,
                ChapterOutline.novel_version_id == version.id,
            )
            .order_by(ChapterOutline.chapter_num.asc())
        ).scalars().all()

        outline_nums = {int(row.chapter_num) for row in outlines}
        missing_chapters = [row for row in chapters if int(row.chapter_num) not in outline_nums]

        print(f"novel_id={novel.id} version_id={version.id} missing_outlines={[row.chapter_num for row in missing_chapters]}")

        for chapter in missing_chapters:
            summary = str(chapter.summary or "").strip()
            outline_text = summary or _clean_excerpt(chapter.content)
            normalized = upsert_chapter_outline(
                novel.id,
                {
                    "chapter_num": int(chapter.chapter_num),
                    "title": chapter.title,
                    "outline": outline_text,
                    "role": "历史补写目录元数据",
                    "purpose": _purpose_from_summary(summary),
                    "summary": summary or outline_text,
                },
                novel_version_id=version.id,
                db=db,
            )
            print(f"upserted_outline chapter={normalized['chapter_num']} title={normalized['title']}")

        task = _latest_generation_task(db, novel.id)
        legacy_task = _latest_legacy_generation_task(db, novel.id)
        current, total, completed = _repair_task_totals(
            task,
            legacy_task,
            chapter_max=max((int(row.chapter_num) for row in chapters), default=0),
        )

        db.commit()
        print(
            "repair_complete",
            {
                "novel_id": novel.id,
                "version_id": version.id,
                "current_chapter": current,
                "total_chapters": total,
                "completed_chapters": completed,
                "task_public_id": task.public_id if task else None,
                "legacy_task_id": legacy_task.task_id if legacy_task else None,
            },
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
