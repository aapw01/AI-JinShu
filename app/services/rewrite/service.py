"""Domain helpers for versioned chapters and rewrite requests."""
from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.novel import ChapterVersion, NovelVersion, RewriteAnnotation, RewriteRequest


ALLOWED_ISSUE_TYPES = {"bug", "continuity", "style", "pace", "other"}
ALLOWED_PRIORITY = {"must", "should", "nice"}


def ensure_default_version(db: Session, novel_id: int) -> NovelVersion:
    """Ensure novel has at least one default version in version table."""
    existing = db.execute(
        select(NovelVersion)
        .where(NovelVersion.novel_id == novel_id, NovelVersion.is_default == 1)
        .order_by(NovelVersion.version_no.desc())
    ).scalar_one_or_none()
    if existing:
        return existing

    latest = db.execute(
        select(NovelVersion)
        .where(NovelVersion.novel_id == novel_id)
        .order_by(NovelVersion.version_no.desc())
    ).scalar_one_or_none()
    if latest:
        latest.is_default = 1
        db.flush()
        return latest

    version = NovelVersion(novel_id=novel_id, version_no=1, status="draft", is_default=1)
    db.add(version)
    db.flush()
    return version


def list_versions(db: Session, novel_id: int) -> list[NovelVersion]:
    ensure_default_version(db, novel_id)
    return db.execute(
        select(NovelVersion)
        .where(NovelVersion.novel_id == novel_id)
        .order_by(NovelVersion.version_no.desc())
    ).scalars().all()


def get_version_or_default(db: Session, novel_id: int, version_id: int | None) -> NovelVersion:
    if version_id is not None:
        version = db.execute(
            select(NovelVersion)
            .where(NovelVersion.id == version_id, NovelVersion.novel_id == novel_id)
        ).scalar_one_or_none()
        if version:
            return version
        raise ValueError("version_not_found")

    return ensure_default_version(db, novel_id)


def activate_version(db: Session, novel_id: int, version_id: int) -> NovelVersion:
    version = db.execute(
        select(NovelVersion)
        .where(NovelVersion.id == version_id, NovelVersion.novel_id == novel_id)
    ).scalar_one_or_none()
    if not version:
        raise ValueError("version_not_found")

    rows = db.execute(select(NovelVersion).where(NovelVersion.novel_id == novel_id)).scalars().all()
    for row in rows:
        row.is_default = 1 if row.id == version_id else 0
    db.flush()
    return version


def list_chapter_versions(db: Session, novel_id: int, version_id: int | None) -> tuple[NovelVersion, list[ChapterVersion]]:
    version = get_version_or_default(db, novel_id, version_id)
    rows = db.execute(
        select(ChapterVersion)
        .where(ChapterVersion.novel_version_id == version.id)
        .order_by(ChapterVersion.chapter_num.asc())
    ).scalars().all()
    return version, rows


def get_chapter_version(db: Session, novel_id: int, chapter_num: int, version_id: int | None) -> tuple[NovelVersion, ChapterVersion | None]:
    version = get_version_or_default(db, novel_id, version_id)
    row = db.execute(
        select(ChapterVersion)
        .where(ChapterVersion.novel_version_id == version.id, ChapterVersion.chapter_num == chapter_num)
    ).scalar_one_or_none()
    return version, row


def create_target_version(db: Session, novel_id: int, base_version: NovelVersion, rewrite_from_chapter: int) -> NovelVersion:
    target: NovelVersion | None = None
    for _ in range(3):
        current_max = db.execute(
            select(func.max(NovelVersion.version_no)).where(NovelVersion.novel_id == novel_id)
        ).scalar_one_or_none()
        version_no = int(current_max or 0) + 1
        candidate = NovelVersion(
            novel_id=novel_id,
            version_no=version_no,
            parent_version_id=base_version.id,
            status="generating",
            is_default=0,
        )
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            target = candidate
            break
        except IntegrityError:
            continue
    if not target:
        raise RuntimeError("create_target_version_conflict")

    inherited = db.execute(
        select(ChapterVersion)
        .where(
            ChapterVersion.novel_version_id == base_version.id,
            ChapterVersion.chapter_num < rewrite_from_chapter,
        )
        .order_by(ChapterVersion.chapter_num.asc())
    ).scalars().all()

    for row in inherited:
        db.add(
            ChapterVersion(
                novel_version_id=target.id,
                chapter_num=row.chapter_num,
                title=row.title,
                content=row.content,
                summary=row.summary,
                status=row.status or "completed",
                review_score=row.review_score,
                language_quality_score=row.language_quality_score,
                language_quality_report=row.language_quality_report,
                metadata_=row.metadata_ or {},
                source_chapter_version_id=row.id,
            )
        )
    db.flush()
    return target


def get_default_version_id(db: Session, novel_id: int) -> int:
    """Return current default version id for a novel."""
    version = ensure_default_version(db, novel_id)
    return int(version.id)


def validate_annotation_payload(content: str, start_offset: int | None, end_offset: int | None, selected_text: str | None) -> None:
    if start_offset is None and end_offset is None and not selected_text:
        return

    text = content or ""
    if start_offset is not None and start_offset < 0:
        raise ValueError("invalid_start_offset")
    if end_offset is not None and end_offset < 0:
        raise ValueError("invalid_end_offset")
    if start_offset is not None and end_offset is not None and end_offset < start_offset:
        raise ValueError("invalid_range")

    if start_offset is not None and end_offset is not None and end_offset <= len(text):
        span = text[start_offset:end_offset]
        if selected_text and span != selected_text:
            raise ValueError("selected_text_mismatch")
        return

    if selected_text and selected_text not in text:
        raise ValueError("selected_text_not_found")


def persist_annotations(
    db: Session,
    rewrite_request: RewriteRequest,
    novel_id: int,
    base_version_id: int,
    annotations: list[dict],
) -> None:
    for item in annotations:
        issue_type = str(item.get("issue_type") or "other").strip()
        priority = str(item.get("priority") or "should").strip()
        if issue_type not in ALLOWED_ISSUE_TYPES:
            issue_type = "other"
        if priority not in ALLOWED_PRIORITY:
            priority = "should"
        db.add(
            RewriteAnnotation(
                rewrite_request_id=rewrite_request.id,
                novel_id=novel_id,
                base_version_id=base_version_id,
                chapter_num=int(item["chapter_num"]),
                start_offset=item.get("start_offset"),
                end_offset=item.get("end_offset"),
                selected_text=item.get("selected_text"),
                issue_type=issue_type,
                instruction=str(item.get("instruction") or "").strip(),
                priority=priority,
                metadata_=item.get("metadata") or {},
            )
        )


def group_annotations_by_chapter(db: Session, request_id: int) -> dict[int, list[RewriteAnnotation]]:
    rows = db.execute(
        select(RewriteAnnotation)
        .where(RewriteAnnotation.rewrite_request_id == request_id)
        .order_by(RewriteAnnotation.chapter_num.asc(), RewriteAnnotation.id.asc())
    ).scalars().all()
    out: dict[int, list[RewriteAnnotation]] = {}
    for row in rows:
        out.setdefault(row.chapter_num, []).append(row)
    return out
