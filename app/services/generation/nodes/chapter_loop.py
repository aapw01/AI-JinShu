"""Chapter-loop nodes: context loading, beats, consistency check, save-blocked, advance."""
from __future__ import annotations

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.novel import ChapterVersion
from app.services.generation.common import resolve_chapter_title
from app.services.generation.heuristics import build_consistency_scorecard
from app.services.generation.progress import chapter_progress, progress, volume_no_for_chapter
from app.services.generation.state import GenerationState


def node_load_context(state: GenerationState) -> GenerationState:
    from app.services.memory.context import build_chapter_context

    chapter_num = state["current_chapter"]
    outlines = state.get("full_outlines", [])
    outline_map = {o.get("chapter_num"): o for o in outlines if isinstance(o, dict)}
    outline = outline_map.get(chapter_num)
    if outline is None:
        idx = chapter_num - state["start_chapter"]
        outline = outlines[idx] if 0 <= idx < len(outlines) else None
    if outline is None:
        outline = {"chapter_num": chapter_num, "title": f"第{chapter_num}章", "outline": ""}

    db = SessionLocal()
    try:
        progress(state, "context", chapter_num, chapter_progress(state, 0.10), "加载分层上下文...", {"current_phase": "chapter_writing", "total_chapters": state["num_chapters"]})
        ctx = build_chapter_context(
            state["novel_id"],
            state.get("novel_version_id"),
            chapter_num,
            state["prewrite"],
            outline,
            db=db,
            volume_size=int(state.get("volume_size") or 30),
        )
        ctx["prewrite"] = state["prewrite"]
        ctx["chapter_outline"] = outline
        ctx["volume_plan"] = state.get("volume_plan") or {}
        ctx["closure_state"] = state.get("closure_state") or {}
        ctx["decision_state"] = state.get("decision_state") or {}
        ctx["prompt_contract"] = {
            "NarrativeIntent": {
                "chapter_goal": str(outline.get("purpose") or "推进主线并形成阶段性兑现"),
                "conflict_target": str(outline.get("plot_twist_level") or "中"),
                "payoff_target": str(outline.get("payoff") or "无"),
            },
            "ClosureIntent": {
                "phase_mode": str((state.get("closure_state") or {}).get("phase_mode") or ""),
                "must_close_items": (state.get("closure_state") or {}).get("must_close_items") or [],
                "suppress_new_mainline": str((state.get("closure_state") or {}).get("phase_mode") or "") in {"closing", "finale"},
            },
            "PacingIntent": {
                "mode": str(state.get("pacing_mode") or "normal"),
                "min_progress_signal": 0.45,
                "streak": int(state.get("low_progress_streak") or 0),
            },
            "HardConstraints": {
                "consistency": state["bible_store"].get_chapter_constraints(
                    state["novel_id"],
                    chapter_num,
                    novel_version_id=state.get("novel_version_id"),
                    db=db,
                ),
            },
            "AntiRepeatIntent": ctx.get("anti_repeat_constraints") or {},
            "TransitionIntent": ctx.get("transition_constraints") or {},
        }
        ctx["hard_constraints"] = ctx["prompt_contract"]["HardConstraints"]["consistency"]
        ctx["character_states"] = state["char_mgr"].get_states(
            state["novel_id"],
            chapter_num,
            db=db,
            novel_version_id=state.get("novel_version_id"),
        )
        ctx["summaries"] = state["summary_mgr"].get_summaries_before(
            state["novel_id"],
            state.get("novel_version_id"),
            chapter_num,
            db=db,
        )
    finally:
        db.close()
    return {
        "outline": outline,
        "context": ctx,
        "draft": "",
        "candidate_drafts": [],
        "feedback": "",
        "factual_feedback": "",
        "aesthetic_feedback": "",
        "score": 0.0,
        "factual_score": 0.0,
        "aesthetic_review_score": 0.0,
        "review_attempt": 0,
        "rerun_count": 0,
        "chapter_token_snapshot": {"input": state["total_input_tokens"], "output": state["total_output_tokens"]},
    }


def node_beats(state: GenerationState) -> GenerationState:
    chapter_num = state["current_chapter"]
    outline = state.get("outline") or {}
    beats = [
        {"name": "hook", "target": str(outline.get("hook") or "开场钩子")},
        {"name": "conflict", "target": str(outline.get("purpose") or "冲突升级")},
        {"name": "turn", "target": str(outline.get("plot_twist_level") or "中段转折")},
        {"name": "payoff", "target": str(outline.get("payoff") or "阶段兑现")},
        {"name": "cliffhanger", "target": str(outline.get("suspense_level") or "章末悬念")},
    ]
    quality_focus = ((state.get("volume_plan") or {}).get("quality_focus") or [])
    replan_actions = ((state.get("volume_plan") or {}).get("replan_actions") or [])
    if quality_focus:
        beats.append({"name": "quality_fix", "target": "；".join(str(x) for x in quality_focus[:2])})
    if replan_actions:
        beats.append({"name": "replan_action", "target": "；".join(str(x) for x in replan_actions[:2])})
    closure_state = state.get("closure_state") or {}
    closure_items = closure_state.get("must_close_items") or []
    closure_phase = str(closure_state.get("phase_mode") or "")
    pacing_mode = str(state.get("pacing_mode") or "normal")
    low_progress_streak = int(state.get("low_progress_streak") or 0)
    if closure_phase in {"closing", "finale"}:
        beats.append({"name": "ending_mode", "target": "收官阶段：减少新支线，优先闭环主线冲突与高优先伏笔。"})
    if pacing_mode in {"accelerated", "closing_accelerated"}:
        beats.append({"name": "pace_boost", "target": "连续低推进触发加速：本章必须出现不可逆变化与冲突升级。"})
        beats.append({"name": "payoff_boost", "target": "至少兑现1个既有伏笔/矛盾，禁止空转铺垫。"})
        if low_progress_streak >= 3:
            beats.append({"name": "hard_hook", "target": "章末必须形成强钩子，且直接连接下一章主冲突。"})
    if closure_items:
        labels = [str(x.get("title") or x.get("id") or "") for x in closure_items[:2] if x]
        if labels:
            beats.append({"name": "closure_target", "target": "本章优先回收：" + "；".join(labels)})
    ctx = dict(state["context"])
    ctx["beat_sheet"] = beats
    contract = dict(ctx.get("prompt_contract") or {})
    contract["NarrativeIntent"] = {
        "chapter_goal": str(outline.get("purpose") or "推进主线并形成阶段兑现"),
        "conflict_target": str(outline.get("plot_twist_level") or "中"),
        "payoff_target": str(outline.get("payoff") or "无"),
        "beats": beats,
    }
    contract["PacingIntent"] = {
        "mode": pacing_mode,
        "streak": low_progress_streak,
        "min_progress_signal": 0.45,
    }
    contract["ClosureIntent"] = {
        "phase_mode": closure_phase,
        "must_close_items": closure_items[:3],
        "suppress_new_mainline": closure_phase in {"closing", "finale"},
    }
    ctx["prompt_contract"] = contract
    progress(
        state,
        "beats",
        chapter_num,
        chapter_progress(state, 0.25),
        f"第{chapter_num}章节拍卡已生成",
        {"current_phase": "chapter_beats", "total_chapters": state["num_chapters"]},
    )
    return {"context": ctx}


def node_consistency_check(state: GenerationState) -> GenerationState:
    from app.services.generation.consistency import check_consistency, inject_consistency_context

    chapter_num = state["current_chapter"]
    progress(state, "consistency", chapter_num, chapter_progress(state, 0.15), "一致性检查...", {"current_phase": "consistency_check", "total_chapters": state["num_chapters"]})
    report = check_consistency(
        state["novel_id"],
        state["novel_version_id"],
        chapter_num,
        state["outline"],
        state["context"],
        state["prewrite"],
    )
    scorecard = build_consistency_scorecard(report)
    ctx = inject_consistency_context(state["context"], report)
    return {
        "consistency_report": report,
        "consistency_scorecard": scorecard,
        "context": ctx,
        "consistency_soft_fail": True,  # always soft-fail: never skip chapters
    }


def node_save_blocked(state: GenerationState) -> GenerationState:
    chapter_num = state["current_chapter"]
    report = state["consistency_report"]
    chapter_title = resolve_chapter_title(
        chapter_num=chapter_num,
        title=(state.get("outline") or {}).get("title"),
        outline=state.get("outline") or {},
    )
    db = SessionLocal()
    try:
        existing_stmt = select(ChapterVersion).where(
            ChapterVersion.novel_version_id == state.get("novel_version_id"),
            ChapterVersion.chapter_num == chapter_num,
        )
        existing = db.execute(existing_stmt).scalar_one_or_none()
        payload = {
            "title": chapter_title,
            "content": "",
            "summary": "",
            "status": "consistency_blocked",
            "metadata_": {"consistency_report": report.summary(), "consistency_blocked": True},
        }
        if existing:
            for k, v in payload.items():
                setattr(existing, k, v)
        else:
            db.add(
                ChapterVersion(
                    novel_version_id=state.get("novel_version_id"),
                    chapter_num=chapter_num,
                    **payload,
                )
            )
        db.commit()
    finally:
        db.close()
    vol_no = volume_no_for_chapter(state, chapter_num)
    state["quality_store"].add_report(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        scope="chapter",
        scope_id=str(chapter_num),
        metrics_json={
            "blocked": True,
            "volume_no": vol_no,
            "reason": report.summary(),
            "consistency_scorecard": state.get("consistency_scorecard") or {},
        },
        verdict="fail",
    )
    if state.get("task_id"):
        state["checkpoint_store"].save_checkpoint(
            task_id=state["task_id"],
            novel_id=state["novel_id"],
            volume_no=vol_no,
            chapter_num=chapter_num,
            node="consistency_blocked",
            state_json={
                "reason": report.summary(),
                "consistency_scorecard": state.get("consistency_scorecard") or {},
            },
        )
    progress(state, "chapter_blocked", chapter_num, chapter_progress(state, 1.0), f"第{chapter_num}章因一致性检查未通过已跳过", {"current_phase": "chapter_blocked", "total_chapters": state["num_chapters"]})
    return {"outline": {**(state.get("outline") or {}), "title": chapter_title}}


def node_advance_chapter(state: GenerationState) -> GenerationState:
    return {"current_chapter": state["current_chapter"] + 1}
