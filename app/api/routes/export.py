"""Export routes - txt, markdown, zip."""
import io
import json
import zipfile
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authz.deps import require_permission
from app.core.authz.resources import load_novel_resource
from app.core.authz.types import Permission, Principal
from app.core.database import get_db, resolve_novel
from app.models.novel import ChapterOutline, ChapterVersion, Novel, NovelSpecification
from app.services.rewrite.service import get_version_or_default

router = APIRouter()


def _build_txt(novel: Novel, chapters: list[ChapterVersion]) -> str:
    lines = [f"# {novel.title}\n"]
    for c in chapters:
        lines.append(f"\n\n## 第{c.chapter_num}章 {c.title or ''}\n\n")
        lines.append(c.content or "")
    return "\n".join(lines)


def _chapter_file_name(chapter: ChapterVersion) -> str:
    safe_title = (chapter.title or "").replace("/", "_").replace("\\", "_").strip()
    return f"{chapter.chapter_num:03d}_{safe_title or 'chapter'}.txt"


def _list_outlines_for_version(db: Session, novel_id: int, version_id: int) -> list[ChapterOutline]:
    stmt = (
        select(ChapterOutline)
        .where(
            ChapterOutline.novel_id == novel_id,
            ChapterOutline.novel_version_id == version_id,
        )
        .order_by(ChapterOutline.chapter_num)
    )
    rows = db.execute(stmt).scalars().all()
    if rows:
        return rows
    fallback = (
        select(ChapterOutline)
        .where(
            ChapterOutline.novel_id == novel_id,
            ChapterOutline.novel_version_id.is_(None),
        )
        .order_by(ChapterOutline.chapter_num)
    )
    return db.execute(fallback).scalars().all()


@router.get("/{novel_id}/export")
def export_novel(
    novel_id: str,
    format: str = "txt",
    version_id: int | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    """Export novel as txt, md, or zip."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    try:
        version = get_version_or_default(db, novel.id, version_id)
    except ValueError:
        raise HTTPException(404, "Version not found")
    chapter_stmt = (
        select(ChapterVersion)
        .where(ChapterVersion.novel_version_id == version.id)
        .order_by(ChapterVersion.chapter_num)
    )
    chapters = db.execute(chapter_stmt).scalars().all()
    if not chapters:
        raise HTTPException(409, "No chapters in target version")

    if format == "txt":
        content = _build_txt(novel, chapters)
        return PlainTextResponse(content, media_type="text/plain; charset=utf-8")

    if format == "md" or format == "markdown":
        content = _build_txt(novel, chapters)
        return PlainTextResponse(content, media_type="text/markdown; charset=utf-8")

    if format == "zip":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "00_版本信息.json",
                json.dumps(
                    {
                        "novel_title": novel.title,
                        "novel_id": novel.uuid or str(novel.id),
                        "version_id": version.id,
                        "version_no": version.version_no,
                        "is_default": bool(version.is_default),
                        "status": version.status,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            zf.writestr("01_小说信息.txt", f"标题: {novel.title}\n类型: {novel.genre or ''}\n状态: {novel.status}\n")
            for c in chapters:
                zf.writestr(_chapter_file_name(c), c.content or "")
            outlines = _list_outlines_for_version(db, novel.id, version.id)
            if outlines:
                outline_payload = [
                    {
                        "chapter_num": o.chapter_num,
                        "title": o.title,
                        "outline": o.outline,
                        "metadata": o.metadata_ or {},
                    }
                    for o in outlines
                ]
                zf.writestr("02_chapter_outlines.json", json.dumps(outline_payload, ensure_ascii=False, indent=2))
            final_stmt = select(NovelSpecification).where(
                NovelSpecification.novel_id == novel.id,
                NovelSpecification.spec_type == "final_book_review",
            )
            final_review = db.execute(final_stmt).scalar_one_or_none()
            if final_review and isinstance(final_review.content, dict):
                zf.writestr("03_final_book_review.json", json.dumps(final_review.content, ensure_ascii=False, indent=2))
            zf.writestr(f"{novel.title}.md", _build_txt(novel, chapters))
        buffer.seek(0)
        encoded_filename = quote(f"{novel.title}.zip")
        return StreamingResponse(
            buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
        )

    raise HTTPException(400, "format must be txt, md, or zip")
