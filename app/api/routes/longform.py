"""Long-form generation support routes."""
from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db, resolve_novel
from app.models.novel import QualityReport, GenerationCheckpoint, Chapter, StorySnapshot, NovelFeedback

router = APIRouter()


@router.get("/{novel_id}/quality-reports")
def list_quality_reports(
    novel_id: str,
    scope: str | None = None,
    scope_id: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """List quality reports for chapter/volume/book scopes."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    stmt = select(QualityReport).where(QualityReport.novel_id == novel.id).order_by(QualityReport.created_at.desc())
    if scope:
        stmt = stmt.where(QualityReport.scope == scope)
    if scope_id:
        stmt = stmt.where(QualityReport.scope_id == scope_id)
    rows = db.execute(stmt.limit(max(1, min(limit, 1000)))).scalars().all()
    return [
        {
            "id": r.id,
            "scope": r.scope,
            "scope_id": r.scope_id,
            "verdict": r.verdict,
            "metrics": r.metrics_json or {},
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ]


@router.get("/{novel_id}/checkpoints")
def list_generation_checkpoints(
    novel_id: str,
    task_id: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """List durable generation checkpoints."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    stmt = (
        select(GenerationCheckpoint)
        .where(GenerationCheckpoint.novel_id == novel.id)
        .order_by(GenerationCheckpoint.chapter_num.desc(), GenerationCheckpoint.id.desc())
    )
    if task_id:
        stmt = stmt.where(GenerationCheckpoint.task_id == task_id)
    rows = db.execute(stmt.limit(max(1, min(limit, 1000)))).scalars().all()
    return [
        {
            "id": r.id,
            "task_id": r.task_id,
            "volume_no": r.volume_no,
            "chapter_num": r.chapter_num,
            "node": r.node,
            "state": r.state_json or {},
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ]


@router.get("/{novel_id}/volumes/summary")
def get_volume_summary(
    novel_id: str,
    volume_size: int = 30,
    db: Session = Depends(get_db),
):
    """Return volume-level completion summary."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    volume_size = max(1, min(volume_size, 200))

    ch_stmt = select(Chapter.chapter_num).where(Chapter.novel_id == novel.id).order_by(Chapter.chapter_num)
    chapter_nums = [r[0] for r in db.execute(ch_stmt).all()]
    if not chapter_nums:
        return []

    latest_chapter = max(chapter_nums)
    total_volumes = ((latest_chapter - 1) // volume_size) + 1
    result: list[dict] = []

    for volume_no in range(1, total_volumes + 1):
        start_ch = (volume_no - 1) * volume_size + 1
        end_ch = min(volume_no * volume_size, latest_chapter)
        completed = len([x for x in chapter_nums if start_ch <= x <= end_ch])

        snap_stmt = (
            select(StorySnapshot)
            .where(StorySnapshot.novel_id == novel.id, StorySnapshot.volume_no == volume_no)
            .order_by(StorySnapshot.id.desc())
        )
        snapshot = db.execute(snap_stmt).scalars().first()

        q_stmt = (
            select(QualityReport)
            .where(
                QualityReport.novel_id == novel.id,
                QualityReport.scope == "volume",
                QualityReport.scope_id == str(volume_no),
            )
            .order_by(QualityReport.id.desc())
        )
        q_report = db.execute(q_stmt).scalars().first()

        result.append(
            {
                "volume_no": volume_no,
                "start_chapter": start_ch,
                "end_chapter": end_ch,
                "completed_chapters": completed,
                "target_chapters": end_ch - start_ch + 1,
                "snapshot_id": snapshot.id if snapshot else None,
                "quality_verdict": q_report.verdict if q_report else None,
            }
        )
    return result


@router.get("/{novel_id}/volumes/{volume_no}/gate-report")
def get_volume_gate_report(
    novel_id: str,
    volume_no: int,
    db: Session = Depends(get_db),
):
    """Return the latest volume gate report (quality + checkpoint evidence)."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    if volume_no <= 0:
        raise HTTPException(400, "volume_no must be positive")

    q_stmt = (
        select(QualityReport)
        .where(
            QualityReport.novel_id == novel.id,
            QualityReport.scope == "volume",
            QualityReport.scope_id == str(volume_no),
        )
        .order_by(QualityReport.id.desc())
    )
    q_report = db.execute(q_stmt).scalars().first()

    cp_stmt = (
        select(GenerationCheckpoint)
        .where(
            GenerationCheckpoint.novel_id == novel.id,
            GenerationCheckpoint.volume_no == volume_no,
            GenerationCheckpoint.node == "volume_gate",
        )
        .order_by(GenerationCheckpoint.id.desc())
    )
    cp = db.execute(cp_stmt).scalars().first()

    if not q_report and not cp:
        raise HTTPException(404, "Volume gate report not found")

    metrics = (q_report.metrics_json or {}) if q_report else {}
    cp_state = (cp.state_json or {}) if cp else {}
    evidence_chain = metrics.get("evidence_chain") or cp_state.get("evidence_chain") or []

    return {
        "volume_no": volume_no,
        "verdict": (q_report.verdict if q_report else cp_state.get("verdict", "unknown")),
        "metrics": metrics,
        "evidence_chain": evidence_chain,
        "checkpoint_id": cp.id if cp else None,
        "checkpoint_state": cp_state,
        "created_at": (q_report.created_at.isoformat() if q_report and q_report.created_at else ""),
    }


@router.get("/{novel_id}/feedback")
def list_feedback(
    novel_id: str,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    stmt = (
        select(NovelFeedback)
        .where(NovelFeedback.novel_id == novel.id)
        .order_by(NovelFeedback.id.desc())
        .limit(max(1, min(limit, 1000)))
    )
    rows = db.execute(stmt).scalars().all()
    return [
        {
            "id": r.id,
            "chapter_num": r.chapter_num,
            "volume_no": r.volume_no,
            "feedback_type": r.feedback_type,
            "rating": r.rating,
            "tags": r.tags or [],
            "comment": r.comment or "",
            "created_at": r.created_at.isoformat() if r.created_at else "",
        }
        for r in rows
    ]


@router.post("/{novel_id}/feedback")
def create_feedback(
    novel_id: str,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
):
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    row = NovelFeedback(
        novel_id=novel.id,
        chapter_num=payload.get("chapter_num"),
        volume_no=payload.get("volume_no"),
        feedback_type=str(payload.get("feedback_type") or "editor"),
        rating=float(payload["rating"]) if payload.get("rating") is not None else None,
        tags=payload.get("tags") if isinstance(payload.get("tags"), list) else [],
        comment=str(payload.get("comment") or ""),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "chapter_num": row.chapter_num,
        "volume_no": row.volume_no,
        "feedback_type": row.feedback_type,
        "rating": row.rating,
        "tags": row.tags or [],
        "comment": row.comment or "",
    }


@router.get("/{novel_id}/observability")
def get_observability(
    novel_id: str,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """Unified observability payload for quality/checkpoint/feedback trend."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    max_limit = max(1, min(limit, 500))

    q_stmt = (
        select(QualityReport)
        .where(QualityReport.novel_id == novel.id)
        .order_by(QualityReport.id.desc())
        .limit(max_limit)
    )
    c_stmt = (
        select(GenerationCheckpoint)
        .where(GenerationCheckpoint.novel_id == novel.id)
        .order_by(GenerationCheckpoint.id.desc())
        .limit(max_limit)
    )
    f_stmt = (
        select(NovelFeedback)
        .where(NovelFeedback.novel_id == novel.id)
        .order_by(NovelFeedback.id.desc())
        .limit(max_limit)
    )
    quality_rows = db.execute(q_stmt).scalars().all()
    checkpoint_rows = db.execute(c_stmt).scalars().all()
    feedback_rows = db.execute(f_stmt).scalars().all()

    volume_quality = [r for r in quality_rows if r.scope == "volume"]
    warning_volumes = [r for r in volume_quality if r.verdict in ("warning", "fail")]
    return {
        "summary": {
            "quality_reports": len(quality_rows),
            "checkpoints": len(checkpoint_rows),
            "feedback_count": len(feedback_rows),
            "warning_or_fail_volumes": len(warning_volumes),
        },
        "quality_reports": [
            {
                "id": r.id,
                "scope": r.scope,
                "scope_id": r.scope_id,
                "verdict": r.verdict,
                "metrics": r.metrics_json or {},
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in quality_rows
        ],
        "checkpoints": [
            {
                "id": r.id,
                "task_id": r.task_id,
                "volume_no": r.volume_no,
                "chapter_num": r.chapter_num,
                "node": r.node,
                "state": r.state_json or {},
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in checkpoint_rows
        ],
        "feedback": [
            {
                "id": r.id,
                "chapter_num": r.chapter_num,
                "volume_no": r.volume_no,
                "feedback_type": r.feedback_type,
                "rating": r.rating,
                "tags": r.tags or [],
                "comment": r.comment or "",
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
            for r in feedback_rows
        ],
    }
