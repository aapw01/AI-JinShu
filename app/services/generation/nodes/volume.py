"""Volume planning and outline confirmation gate nodes."""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.creation_task import CreationTask
from app.models.novel import GenerationTask, Novel, NovelFeedback
from app.services.generation.common import REVIEW_SCORE_THRESHOLD
from app.services.generation.progress import chapter_progress, progress, volume_no_for_chapter
from app.services.generation.state import GenerationState


def node_volume_replan(state: GenerationState) -> GenerationState:
    """Build per-volume plan at volume boundaries."""
    chapter_num = state["current_chapter"]
    vol_no = volume_no_for_chapter(state, chapter_num)
    volume_size = max(int(state.get("volume_size") or 30), 1)
    start = chapter_num
    end = min(state["end_chapter"], start + volume_size - 1)
    outlines = [o for o in (state.get("full_outlines") or []) if start <= int(o.get("chapter_num", 0)) <= end]

    previous_volume = max(0, vol_no - 1)
    quality_focus: list[str] = []
    previous_quality: dict[str, Any] = {}
    previous_snapshot: dict[str, Any] = {}
    previous_verdict = "pass"
    replan_level = "baseline"
    replan_actions = ["保持主线推进与人物动机一致。"]
    gate_evidence: list[dict[str, Any]] = []
    if previous_volume > 0:
        prev_reports = state["quality_store"].list_reports(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            scope="volume",
            scope_id=str(previous_volume),
        )
        if prev_reports:
            previous_quality = prev_reports[0].metrics_json or {}
            previous_verdict = str(getattr(prev_reports[0], "verdict", "pass") or "pass")
            avg_review = float(previous_quality.get("avg_review_score") or 0.0)
            avg_language = float(previous_quality.get("avg_language_score") or 0.0)
            avg_aesthetic = float(previous_quality.get("avg_aesthetic_score") or 0.0)
            gate_evidence = previous_quality.get("evidence_chain") or []
            if avg_review < REVIEW_SCORE_THRESHOLD:
                quality_focus.append("强化单章主冲突与阶段性兑现，减少平铺叙事。")
            if avg_language < 0.65:
                quality_focus.append("优化句式节奏与口语自然度，控制说明性段落密度。")
            if avg_aesthetic < 0.62:
                quality_focus.append("提升情绪张力，保证章末悬念与反转落点。")
            if previous_verdict == "fail":
                replan_level = "aggressive"
                replan_actions = [
                    "限制支线数量，优先回收高优先级伏笔。",
                    "每3章至少一次强兑现并推进主线不可逆变化。",
                    "减少解释性段落，增加场景化冲突与动作。",
                ]
            elif previous_verdict == "warning" or quality_focus:
                replan_level = "focus"
                replan_actions = [
                    "围绕上一卷低分指标执行定向修正。",
                    "保持既有世界观与角色声纹稳定。",
                ]
        prev_snapshot = state["bible_store"].get_latest_snapshot(state["novel_id"], previous_volume)
        if prev_snapshot and isinstance(prev_snapshot.snapshot_json, dict):
            previous_snapshot = prev_snapshot.snapshot_json
        db = SessionLocal()
        try:
            fb_stmt = (
                select(NovelFeedback)
                .where(
                    NovelFeedback.novel_id == state["novel_id"],
                    NovelFeedback.volume_no == previous_volume,
                )
                .order_by(NovelFeedback.id.desc())
                .limit(10)
            )
            feedback_rows = db.execute(fb_stmt).scalars().all()
            feedback_tags = [str(t) for r in feedback_rows for t in (r.tags or []) if t]
            if feedback_tags:
                quality_focus.append(f"编辑反馈关注点: {'/'.join(feedback_tags[:6])}")
        finally:
            db.close()

    constraints = state["bible_store"].get_chapter_constraints(
        state["novel_id"],
        chapter_num,
        novel_version_id=state.get("novel_version_id"),
    )
    carry_over = [
        {
            "foreshadow_id": str(item.get("foreshadow_id") or ""),
            "title": str(item.get("title") or "")[:160],
            "planted_chapter": int(item.get("planted_chapter") or 0),
        }
        for item in (constraints.get("unresolved_foreshadows") or [])[:10]
        if item
    ]
    chapter_targets = [
        {"chapter_num": int(o.get("chapter_num", 0)), "title": o.get("title"), "goal": o.get("purpose") or "推进主线"}
        for o in outlines
    ]
    if quality_focus or replan_level != "baseline":
        for target in chapter_targets:
            goal = str(target.get("goal") or "")
            focus_text = " ".join(quality_focus) if quality_focus else "保持上一卷修正策略"
            target["goal"] = f"{goal}；质量修正: {focus_text}".strip("；")

    volume_plan = {
        "volume_no": vol_no,
        "start_chapter": start,
        "end_chapter": end,
        "theme": f"Volume-{vol_no}",
        "chapter_targets": chapter_targets,
        "quality_focus": quality_focus,
        "carry_over_foreshadows": carry_over,
        "previous_volume_quality": previous_quality,
        "previous_volume_verdict": previous_verdict,
        "replan_level": replan_level,
        "replan_actions": replan_actions,
        "gate_evidence": gate_evidence,
        "previous_volume_snapshot": previous_snapshot,
    }
    progress(
        state,
        "volume_replan",
        chapter_num,
        chapter_progress(state, 0.05),
        f"生成第{vol_no}卷执行计划",
        {
            "current_phase": "volume_planning",
            "total_chapters": state["num_chapters"],
            "volume_no": vol_no,
            "replan_level": replan_level,
        },
    )
    if state.get("task_id"):
        state["checkpoint_store"].save_checkpoint(
            task_id=state["task_id"],
            novel_id=state["novel_id"],
            volume_no=vol_no,
            chapter_num=chapter_num,
            node="volume_replan",
            state_json=volume_plan,
        )
    return {"volume_no": vol_no, "volume_plan": volume_plan}


def node_confirmation_gate(state: GenerationState) -> GenerationState:
    if not state.get("task_id"):
        return {}
    db = SessionLocal()
    try:
        novel_stmt = select(Novel).where(Novel.id == state["novel_id"])
        novel_row = db.execute(novel_stmt).scalar_one_or_none()
        require_confirm = bool((novel_row.config or {}).get("require_outline_confirmation"))
        if not require_confirm:
            return {}
        ct = None
        if state.get("creation_task_id"):
            ct = db.execute(
                select(CreationTask).where(CreationTask.id == int(state["creation_task_id"]))
            ).scalar_one_or_none()
        gt_stmt = select(GenerationTask).where(GenerationTask.task_id == state["task_id"])
        gt = db.execute(gt_stmt).scalar_one_or_none()
        if ct:
            payload = dict(ct.payload_json) if isinstance(ct.payload_json, dict) else {}
            payload["awaiting_outline_confirmation"] = True
            payload["outline_confirmed"] = False
            ct.payload_json = payload
            ct.phase = "outline_ready"
            ct.message = "章节大纲已生成，等待确认"
        if gt:
            gt.status = "awaiting_outline_confirmation"
            gt.current_phase = "outline_ready"
            gt.outline_confirmed = 0
            gt.message = "章节大纲已生成，等待确认"
        if ct or gt:
            novel_row.status = "awaiting_outline_confirmation"
            db.commit()
            progress(
                state,
                "outline_waiting_confirmation",
                0,
                20,
                "等待用户确认大纲后继续生成",
                {"status": "awaiting_outline_confirmation", "current_phase": "outline_ready", "total_chapters": state["num_chapters"]},
            )
        while ct:
            db.refresh(ct)
            payload = dict(ct.payload_json) if isinstance(ct.payload_json, dict) else {}
            if bool(payload.get("outline_confirmed")):
                break
            time.sleep(2)
        while gt and gt.outline_confirmed != 1:
            db.refresh(gt)
            time.sleep(2)
        if ct:
            payload = dict(ct.payload_json) if isinstance(ct.payload_json, dict) else {}
            payload["awaiting_outline_confirmation"] = False
            payload["outline_confirmed"] = True
            ct.payload_json = payload
            ct.phase = "chapter_writing"
            ct.message = "已确认大纲，继续生成章节"
        if gt:
            gt.status = "running"
            gt.current_phase = "chapter_writing"
            gt.message = "已确认大纲，继续生成章节"
        if ct or gt:
            novel_row.status = "generating"
            db.commit()
    finally:
        db.close()
    return {}
