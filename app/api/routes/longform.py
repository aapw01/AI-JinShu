"""Long-form generation support routes."""
from collections import defaultdict

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.core.api_errors import http_error
from app.core.authz.deps import require_permission
from app.core.authz.resources import load_novel_resource
from app.core.authz.types import Permission, Principal
from app.core.database import get_db, resolve_novel
from app.core.time_utils import to_utc_iso_z
from app.models.novel import ChapterVersion, GenerationCheckpoint, NovelFeedback, NovelVersion, QualityReport, StorySnapshot
from app.services.generation.evaluation_metrics import (
    compute_abrupt_ending_risk,
    compute_closure_action_metrics,
)

router = APIRouter()


def _resolve_version_or_400(db: Session, *, novel_id: int, version_id: int | None) -> NovelVersion:
    """根据请求里的版本 ID 找到对应版本；缺失或越权时抛出错误。"""
    if version_id is None:
        raise http_error(400, "missing_version_id", "version_id is required")
    version = db.execute(
        select(NovelVersion).where(
            NovelVersion.id == int(version_id),
            NovelVersion.novel_id == int(novel_id),
        )
    ).scalar_one_or_none()
    if not version:
        raise http_error(404, "version_not_found", "Version not found")
    return version


@router.get("/{novel_id}/quality-reports")
def list_quality_reports(
    novel_id: str,
    scope: str | None = None,
    scope_id: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
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
            "created_at": to_utc_iso_z(r.created_at),
        }
        for r in rows
    ]


@router.get("/{novel_id}/checkpoints")
def list_generation_checkpoints(
    novel_id: str,
    task_id: str | None = None,
    limit: int = 200,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
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
            "created_at": to_utc_iso_z(r.created_at),
        }
        for r in rows
    ]


@router.get("/{novel_id}/volumes/summary")
def get_volume_summary(
    novel_id: str,
    volume_size: int = 30,
    version_id: int | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    """Return volume-level completion summary."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    version = _resolve_version_or_400(db, novel_id=novel.id, version_id=version_id)
    volume_size = max(1, min(volume_size, 200))

    ch_stmt = (
        select(ChapterVersion.chapter_num)
        .where(ChapterVersion.novel_version_id == version.id)
        .order_by(ChapterVersion.chapter_num)
    )
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
            .where(
                StorySnapshot.novel_id == novel.id,
                StorySnapshot.novel_version_id == version.id,
                StorySnapshot.volume_no == volume_no,
            )
            .order_by(StorySnapshot.id.desc())
        )
        snapshot = db.execute(snap_stmt).scalars().first()

        q_stmt = (
            select(QualityReport)
            .where(
                QualityReport.novel_id == novel.id,
                QualityReport.novel_version_id == version.id,
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
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
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
        "created_at": to_utc_iso_z(q_report.created_at if q_report else None),
    }


@router.get("/{novel_id}/closure-report")
def get_closure_report(
    novel_id: str,
    task_id: str | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    """Return latest closure-gate state for ending completeness tracking."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")

    stmt = select(GenerationCheckpoint).where(
        GenerationCheckpoint.novel_id == novel.id,
        GenerationCheckpoint.node == "closure_gate",
    )
    if task_id:
        stmt = stmt.where(GenerationCheckpoint.task_id == task_id)
    stmt = stmt.order_by(GenerationCheckpoint.id.desc())
    cp = db.execute(stmt).scalars().first()
    if not cp:
        return {
            "novel_id": novel.uuid or str(novel.id),
            "task_id": task_id,
            "available": False,
            "message": "closure report not available yet",
            "state": {},
        }
    return {
        "novel_id": novel.uuid or str(novel.id),
        "task_id": cp.task_id,
        "available": True,
        "chapter_num": cp.chapter_num,
        "volume_no": cp.volume_no,
        "state": cp.state_json or {},
        "created_at": to_utc_iso_z(cp.created_at),
    }


@router.get("/{novel_id}/feedback")
def list_feedback(
    novel_id: str,
    limit: int = 200,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    """列出feedback。"""
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
            "created_at": to_utc_iso_z(r.created_at),
        }
        for r in rows
    ]


@router.post("/{novel_id}/feedback")
def create_feedback(
    novel_id: str,
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_UPDATE, resource_loader=load_novel_resource)),
):
    """创建feedback。"""
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
    version_id: int | None = None,
    db: Session = Depends(get_db),
    _: Principal = Depends(require_permission(Permission.NOVEL_READ, resource_loader=load_novel_resource)),
):
    """Unified observability payload for quality/checkpoint/feedback trend."""
    novel = resolve_novel(db, novel_id)
    if not novel:
        raise HTTPException(404, "Novel not found")
    version = _resolve_version_or_400(db, novel_id=novel.id, version_id=version_id)
    max_limit = max(1, min(limit, 500))

    q_stmt = (
        select(QualityReport)
        .where(
            QualityReport.novel_id == novel.id,
            QualityReport.novel_version_id == version.id,
        )
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

    total_quality_reports = db.execute(
        select(func.count()).select_from(QualityReport).where(
            QualityReport.novel_id == novel.id,
            QualityReport.novel_version_id == version.id,
        )
    ).scalar_one()
    total_checkpoints = db.execute(
        select(func.count()).select_from(GenerationCheckpoint).where(GenerationCheckpoint.novel_id == novel.id)
    ).scalar_one()
    total_feedback = db.execute(
        select(func.count()).select_from(NovelFeedback).where(NovelFeedback.novel_id == novel.id)
    ).scalar_one()
    total_warning_or_fail_volumes = db.execute(
        select(func.count())
        .select_from(QualityReport)
        .where(
            QualityReport.novel_id == novel.id,
            QualityReport.novel_version_id == version.id,
            QualityReport.scope == "volume",
            QualityReport.verdict.in_(("warning", "fail")),
        )
    ).scalar_one()
    closure_rows_asc = (
        db.execute(
            select(GenerationCheckpoint)
            .where(
                GenerationCheckpoint.novel_id == novel.id,
                GenerationCheckpoint.node == "closure_gate",
            )
            .order_by(GenerationCheckpoint.id.asc())
        )
        .scalars()
        .all()
    )
    closure_actions = [str((r.state_json or {}).get("action") or "") for r in closure_rows_asc]
    closure_metrics = compute_closure_action_metrics(closure_actions)
    latest_closure_state = (closure_rows_asc[-1].state_json or {}) if closure_rows_asc else {}
    tail_chapters = (
        db.execute(
            select(ChapterVersion.content)
            .where(ChapterVersion.novel_version_id == version.id)
            .order_by(ChapterVersion.chapter_num.desc())
            .limit(3)
        )
        .scalars()
        .all()
    )
    abrupt = compute_abrupt_ending_risk(latest_closure_state, tail_chapters)

    node_counts: dict[str, int] = defaultdict(int)
    node_duration_samples: dict[str, list[float]] = defaultdict(list)
    reason_code_counts: dict[str, int] = defaultdict(int)
    task_rows: dict[str, list[GenerationCheckpoint]] = defaultdict(list)
    for row in checkpoint_rows:
        node_counts[str(row.node)] += 1
        task_rows[str(row.task_id or "unknown")].append(row)
        state = row.state_json or {}
        if row.node == "closure_gate":
            for code in (state.get("reason_codes") or []):
                code_str = str(code).strip()
                if code_str:
                    reason_code_counts[code_str] += 1
        if row.node in {"chapter_done", "consistency_blocked"}:
            consistency = state.get("consistency_scorecard") or {}
            for code in (consistency.get("reason_codes") or []):
                code_str = str(code).strip()
                if code_str:
                    reason_code_counts[f"consistency:{code_str}"] += 1

    for _, rows in task_rows.items():
        rows_sorted = sorted(rows, key=lambda x: (x.created_at, x.id))
        for i in range(1, len(rows_sorted)):
            prev = rows_sorted[i - 1]
            cur = rows_sorted[i]
            delta = (cur.created_at - prev.created_at).total_seconds()
            if delta < 0:
                continue
            node_duration_samples[str(cur.node)].append(delta)

    node_latency_seconds: dict[str, dict[str, float | int]] = {}
    for node, samples in node_duration_samples.items():
        if not samples:
            continue
        samples_sorted = sorted(samples)
        p50 = samples_sorted[len(samples_sorted) // 2]
        p95 = samples_sorted[min(len(samples_sorted) - 1, int(len(samples_sorted) * 0.95))]
        node_latency_seconds[node] = {
            "samples": len(samples_sorted),
            "p50": round(float(p50), 3),
            "p95": round(float(p95), 3),
            "max": round(float(samples_sorted[-1]), 3),
        }

    chapter_quality_rows = [r for r in quality_rows if r.scope == "chapter"]
    hard_violation_chapters = 0
    soft_warning_chapters = 0
    over_correction_risk_chapters = 0
    review_gate_accept_minor = 0
    for row in chapter_quality_rows:
        metrics = row.metrics_json or {}
        consistency = metrics.get("consistency_scorecard") or {}
        blockers = int(consistency.get("blockers") or 0)
        warnings = int(consistency.get("warnings") or 0)
        if blockers > 0:
            hard_violation_chapters += 1
        if warnings > 0:
            soft_warning_chapters += 1
        gate = metrics.get("review_gate") or {}
        if bool(gate.get("over_correction_risk")):
            over_correction_risk_chapters += 1
        if str(gate.get("decision") or "") == "accept_with_minor_polish":
            review_gate_accept_minor += 1
    chapter_count = max(1, len(chapter_quality_rows))

    return {
        "summary": {
            "quality_reports": int(total_quality_reports or 0),
            "checkpoints": int(total_checkpoints or 0),
            "feedback_count": int(total_feedback or 0),
            "warning_or_fail_volumes": int(total_warning_or_fail_volumes or 0),
            "closure_action_distribution": closure_metrics.get("distribution") or {},
            "closure_action_oscillation_rate": float(closure_metrics.get("oscillation_rate") or 0.0),
            "abrupt_ending_score": float(abrupt.get("score") or 0.0),
            "abrupt_ending_risk": bool(abrupt.get("is_abrupt")),
            "abrupt_ending_reasons": abrupt.get("reasons") or [],
            "node_counts": dict(node_counts),
            "node_latency_seconds": node_latency_seconds,
            "reason_code_distribution": dict(reason_code_counts),
            "hard_constraint_violation_rate": round(hard_violation_chapters / chapter_count, 4),
            "soft_constraint_warning_rate": round(soft_warning_chapters / chapter_count, 4),
            "review_over_correction_risk_rate": round(over_correction_risk_chapters / chapter_count, 4),
            "review_accept_minor_polish_rate": round(review_gate_accept_minor / chapter_count, 4),
        },
        "quality_reports": [
            {
                "id": r.id,
                "scope": r.scope,
                "scope_id": r.scope_id,
                "verdict": r.verdict,
                "metrics": r.metrics_json or {},
                "created_at": to_utc_iso_z(r.created_at),
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
                "created_at": to_utc_iso_z(r.created_at),
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
                "created_at": to_utc_iso_z(r.created_at),
            }
            for r in feedback_rows
        ],
    }
