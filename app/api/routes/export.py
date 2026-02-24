"""Export routes - txt, markdown, zip."""
import io
import json
import zipfile
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse, PlainTextResponse
from sqlalchemy.orm import Session

from app.core.database import get_db, resolve_novel
from app.models.novel import Novel, Chapter, ChapterOutline, NovelSpecification

router = APIRouter()


def _build_txt(novel: Novel, chapters: list[Chapter]) -> str:
    lines = [f"# {novel.title}\n"]
    for c in chapters:
        lines.append(f"\n\n## 第{c.chapter_num}章 {c.title or ''}\n\n")
        lines.append(c.content or "")
    return "\n".join(lines)


def _chapter_file_name(chapter: Chapter) -> str:
    safe_title = (chapter.title or "").replace("/", "_").replace("\\", "_").strip()
    return f"{chapter.chapter_num:03d}_{safe_title or 'chapter'}.txt"


@router.get("/{novel_id}/export")
def export_novel(novel_id: str, format: str = "txt", db: Session = Depends(get_db)):
    """Export novel as txt, md, or zip."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    chapters = db.query(Chapter).filter(Chapter.novel_id == novel.id).order_by(Chapter.chapter_num).all()

    if format == "txt":
        content = _build_txt(novel, chapters)
        return PlainTextResponse(content, media_type="text/plain; charset=utf-8")

    if format == "md" or format == "markdown":
        content = _build_txt(novel, chapters)
        return PlainTextResponse(content, media_type="text/markdown; charset=utf-8")

    if format == "zip":
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("00_小说信息.txt", f"标题: {novel.title}\n类型: {novel.genre or ''}\n状态: {novel.status}\n")
            for c in chapters:
                zf.writestr(_chapter_file_name(c), c.content or "")
            outlines = (
                db.query(ChapterOutline)
                .filter(ChapterOutline.novel_id == novel.id)
                .order_by(ChapterOutline.chapter_num)
                .all()
            )
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
                zf.writestr("01_chapter_outlines.json", json.dumps(outline_payload, ensure_ascii=False, indent=2))
            final_review = (
                db.query(NovelSpecification)
                .filter(NovelSpecification.novel_id == novel.id, NovelSpecification.spec_type == "final_book_review")
                .first()
            )
            if final_review and isinstance(final_review.content, dict):
                zf.writestr("02_final_book_review.json", json.dumps(final_review.content, ensure_ascii=False, indent=2))
            zf.writestr(f"{novel.title}.md", _build_txt(novel, chapters))
        buffer.seek(0)
        encoded_filename = quote(f"{novel.title}.zip")
        return StreamingResponse(
            buffer,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"},
        )

    raise HTTPException(400, "format must be txt, md, or zip")
