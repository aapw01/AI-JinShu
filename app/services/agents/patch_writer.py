"""Precision rewrite agent (#8)."""

from __future__ import annotations

import logging

from app.core.feature_flags import is_enabled
from app.core.gates import get_gate
from app.services.agents.contracts.patch import PatchInstruction, PatchResult
from app.services.agents.llm_agent import run_llm_agent

logger = logging.getLogger(__name__)


def apply_patch(
    *,
    novel_id: int,
    novel_version_id: int | None,
    chapter_num: int,
    instruction: PatchInstruction,
    chapter_text: str,
) -> PatchResult | None:
    """flag-controlled：用 LLM 在锚点之间局部改写。

    后处理校验：``length_delta`` 必须在 yaml gate 限定的比例内、不得引入新角色、
    必须保留 ``must_keep_characters``。校验失败 → 返回 None（让上游降级）。
    """
    if not is_enabled("repair.precision_rewrite", novel_id=novel_id):
        return None
    try:
        from app.core.metrics import precision_rewrite_attempt_total

        precision_rewrite_attempt_total.inc()
    except Exception:
        pass

    span = instruction.span
    original_text = span.original_text
    if not original_text or not span.anchor_before or not span.anchor_after:
        return None

    parsed = run_llm_agent(
        agent_name="patch_writer",
        event_type="rewrite",
        template="precision_rewrite",
        template_kwargs={
            "original_text": original_text,
            "anchor_before": span.anchor_before,
            "anchor_after": span.anchor_after,
            "instruction": instruction.instruction,
            "must_keep_characters": instruction.must_keep_characters or [],
            "forbid_new_characters": bool(instruction.forbid_new_characters),
        },
        schema=PatchResult,
        novel_id=novel_id,
        chapter_num=chapter_num,
        novel_version_id=novel_version_id,
    )
    if parsed is None:
        return None

    # post-validate
    delta_gate = get_gate("precision_rewrite", "length_delta_max_ratio", novel_id=novel_id)
    max_ratio = float(delta_gate.threshold or 0.3)
    base_len = max(1, len(original_text))
    delta_ratio = abs(parsed.length_delta) / base_len
    if delta_ratio > max_ratio:
        logger.warning(
            "patch_writer: length_delta_ratio=%.2f exceeds max=%.2f", delta_ratio, max_ratio
        )
        return None

    forbid_gate = get_gate("precision_rewrite", "forbid_new_characters", novel_id=novel_id)
    if forbid_gate.mode != "off" and parsed.introduces_new_characters:
        return None

    for required in instruction.must_keep_characters or []:
        if required and required not in parsed.patched_text:
            logger.warning("patch_writer: dropped must_keep=%s", required)
            try:
                from app.core.metrics import precision_rewrite_anchor_miss_total

                precision_rewrite_anchor_miss_total.inc()
            except Exception:
                pass
            return None

    try:
        from app.core.metrics import (
            precision_rewrite_success_total,
            precision_rewrite_token_saved_ratio,
        )

        precision_rewrite_success_total.inc()
        # token saved ratio：原文越长、patch 越短 → ratio 越接近 1
        if base_len > 0:
            ratio = max(0.0, 1.0 - len(parsed.patched_text) / base_len)
            precision_rewrite_token_saved_ratio.observe(ratio)
    except Exception:
        pass
    return parsed


__all__ = ["apply_patch"]
