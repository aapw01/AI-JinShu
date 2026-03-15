"""Writer node — AB-test writing with retry."""
from __future__ import annotations

from typing import Any

from app.core.llm_usage import snapshot_usage
from app.core.strategy import get_model_for_stage
from app.services.generation.contracts import OutputContractError
from app.services.generation.progress import chapter_progress, progress
from app.services.generation.state import GenerationState


def node_writer(state: GenerationState) -> GenerationState:
    def _is_invalid_draft(text: str) -> bool:
        t = str(text or "").strip().lower()
        if not t:
            return True
        return ("content generation failed" in t) or t.startswith("[chapter ")

    def _safe_write(
        chapter_num_: int,
        outline_: dict[str, Any],
        ctx_: dict[str, Any],
        provider_: str | None,
        model_: str | None,
    ) -> str:
        draft = state["writer"].run(
            state["novel_id"],
            chapter_num_,
            outline_,
            ctx_,
            state["target_language"],
            state["native_style_profile"],
            provider_,
            model_,
        )
        if _is_invalid_draft(draft):
            raise RuntimeError("writer returned invalid placeholder draft")
        return draft

    chapter_num = state["current_chapter"]
    attempt = state.get("review_attempt", 0) + 1
    progress(state, "writer", chapter_num, chapter_progress(state, 0.35), f"写作第{chapter_num}章（尝试{attempt}）...", {"current_phase": "chapter_writing", "total_chapters": state["num_chapters"]})
    w_provider, w_model = get_model_for_stage(state["strategy"], "writer")
    pacing_mode = str(state.get("pacing_mode") or "normal")
    ctx_a = dict(state["context"])
    ctx_a["ab_variant"] = "A"
    ctx_a["ab_goal"] = "稳健推进主线，保持事实一致。"
    if pacing_mode in {"accelerated", "closing_accelerated"}:
        ctx_a["ab_goal"] = "加速推进主线，减少铺垫，必须输出明确冲突升级与阶段兑现。"

    def _attempt_ab_write():
        _draft_a = ""
        _err_a: Exception | None = None
        try:
            _draft_a = _safe_write(chapter_num, state["outline"], ctx_a, w_provider, w_model)
        except Exception as exc:
            _err_a = exc

        if _draft_a:
            return _draft_a, "", _err_a, None

        ctx_b = dict(state["context"])
        ctx_b["ab_variant"] = "B"
        ctx_b["ab_goal"] = "增强情绪张力和节奏反转，保持硬约束不变。"
        if pacing_mode in {"accelerated", "closing_accelerated"}:
            ctx_b["ab_goal"] = "强化反转与高压冲突，提升推进效率并压缩无效叙述。"
        _draft_b = ""
        _err_b: Exception | None = None
        try:
            _draft_b = _safe_write(chapter_num, state["outline"], ctx_b, w_provider, w_model)
        except Exception as exc:
            _err_b = exc
        return _draft_a, _draft_b, _err_a, _err_b

    _NODE_WRITER_MAX_ROUNDS = 2
    _NODE_WRITER_ROUND_DELAY = 15.0
    draft_a = ""
    draft_b = ""
    err_a: Exception | None = None
    err_b: Exception | None = None
    for _round in range(_NODE_WRITER_MAX_ROUNDS):
        draft_a, draft_b, err_a, err_b = _attempt_ab_write()
        if draft_a or draft_b:
            break
        if _round < _NODE_WRITER_MAX_ROUNDS - 1:
            import logging as _lg
            _lg.getLogger(__name__).warning(
                "writer AB both failed round=%s, retrying in %.0fs: A=%s B=%s",
                _round + 1, _NODE_WRITER_ROUND_DELAY, err_a, err_b,
            )
            import time as _time
            _time.sleep(_NODE_WRITER_ROUND_DELAY)

    if not draft_a and not draft_b:
        contract_errors = [e for e in (err_a, err_b) if isinstance(e, OutputContractError)]
        if contract_errors:
            detail = f"A={err_a}; B={err_b}"
            if all(e.code == "MODEL_OUTPUT_POLICY_VIOLATION" for e in contract_errors):
                raise OutputContractError(
                    code="MODEL_OUTPUT_POLICY_VIOLATION",
                    stage="writer",
                    chapter_num=chapter_num,
                    provider=w_provider,
                    model=w_model,
                    detail=detail,
                    retryable=False,
                )
            raise OutputContractError(
                code="MODEL_OUTPUT_CONTRACT_EXHAUSTED",
                stage="writer",
                chapter_num=chapter_num,
                provider=w_provider,
                model=w_model,
                detail=detail,
                retryable=True,
            )
        raise RuntimeError(f"writer failed for both variants: A={err_a}, B={err_b}")

    candidates = [
        {"variant": "A", "draft": draft_a} if draft_a else None,
        {"variant": "B", "draft": draft_b} if draft_b else None,
    ]
    candidates = [c for c in candidates if c]
    usage = snapshot_usage()
    input_tokens = int(usage.get("input_tokens") or state["total_input_tokens"] or 0)
    output_tokens = int(usage.get("output_tokens") or state["total_output_tokens"] or 0)
    primary = draft_a or draft_b
    return {"candidate_drafts": candidates, "draft": primary, "total_input_tokens": input_tokens, "total_output_tokens": output_tokens}
