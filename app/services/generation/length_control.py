"""Shared chapter length policy helpers for generation and rewrite."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from app.core.constants import ChapterLengthPolicy, DEFAULT_CHAPTER_LENGTH_POLICY


def count_content_words(content: str) -> int:
    """统计contentwords。"""
    return len("".join(str(content or "").split()))


def resolve_chapter_length_policy(word_count: int | None = None) -> ChapterLengthPolicy:
    """根据目标字数返回本次章节写作应遵循的长度策略。"""
    if word_count is None:
        return DEFAULT_CHAPTER_LENGTH_POLICY
    target_words = int(word_count or DEFAULT_CHAPTER_LENGTH_POLICY.target_words)
    target_words = max(DEFAULT_CHAPTER_LENGTH_POLICY.min_words, target_words)
    target_words = min(DEFAULT_CHAPTER_LENGTH_POLICY.soft_max_words, target_words)
    if target_words == DEFAULT_CHAPTER_LENGTH_POLICY.target_words:
        return DEFAULT_CHAPTER_LENGTH_POLICY
    return replace(DEFAULT_CHAPTER_LENGTH_POLICY, target_words=target_words)


def build_chapter_length_prompt_kwargs(word_count: int | None = None) -> dict[str, int]:
    """构建章节length提示词kwargs。"""
    policy = resolve_chapter_length_policy(word_count)
    ideal_min_word_count = max(policy.min_words, policy.target_words - 400)
    ideal_max_word_count = min(policy.soft_max_words, policy.target_words + 200)
    return {
        "word_count": policy.target_words,
        "min_word_count": policy.min_words,
        "target_word_count": policy.target_words,
        "soft_max_word_count": policy.soft_max_words,
        "hard_ceiling_word_count": policy.hard_ceiling_words,
        "ideal_min_word_count": ideal_min_word_count,
        "ideal_max_word_count": ideal_max_word_count,
    }


def build_length_compaction_feedback(*, before_word_count: int, policy: ChapterLengthPolicy) -> str:
    """构建lengthcompactionfeedback。"""
    severity_line = (
        "当前正文明显超长，请更强约束地压缩重复段落与冗余复述。"
        if before_word_count > policy.hard_ceiling_words
        else "当前正文略超目标，请做最小幅度压缩收口。"
    )
    return (
        "【长度收口】请在不改变剧情走向与文风的前提下压缩正文长度。\n"
        f"- 当前字数：{before_word_count}\n"
        f"- 理想区间：{max(policy.min_words, policy.target_words - 400)}-{min(policy.soft_max_words, policy.target_words + 200)}\n"
        f"- 可接受区间：{policy.min_words}-{policy.soft_max_words}\n"
        f"- 尽量不要超过：{policy.hard_ceiling_words}\n"
        f"{severity_line}\n"
        "要求：\n"
        "1. 只压缩重复心理描写、重复动作回放、重复确认句和解释性复述。\n"
        "2. 保留主冲突、关键兑现、章末钩子和人物动机。\n"
        "3. 不新增剧情，不改时间线，不删关键反转。\n"
        "4. 若已经足够紧凑，只做微调，不要把正文压成骨架摘要。"
    )


def maybe_compact_chapter_length(
    *,
    content: str,
    word_count: int | None = None,
    compact_fn: Callable[[str, str], str],
    normalize_fn: Callable[[str], str],
) -> tuple[str, dict[str, object]]:
    """执行 maybe compact chapter length 相关辅助逻辑。"""
    policy = resolve_chapter_length_policy(word_count)
    before_word_count = count_content_words(content)
    diagnostics: dict[str, object] = {
        "word_count_before_compaction": before_word_count,
        "word_count_after_compaction": before_word_count,
        "length_compaction_attempted": False,
        "length_compaction_applied": False,
        "length_compaction_reason": "",
    }
    if before_word_count <= policy.soft_max_words:
        return content, diagnostics

    diagnostics["length_compaction_attempted"] = True
    diagnostics["length_compaction_reason"] = (
        "hard_ceiling_exceeded"
        if before_word_count > policy.hard_ceiling_words
        else "soft_max_exceeded"
    )
    feedback = build_length_compaction_feedback(before_word_count=before_word_count, policy=policy)
    try:
        compacted = normalize_fn(compact_fn(content, feedback).strip())
    except Exception:
        diagnostics["length_compaction_reason"] = "compaction_error"
        return content, diagnostics

    compacted_word_count = count_content_words(compacted)
    diagnostics["word_count_after_compaction"] = compacted_word_count

    if not compacted.strip():
        diagnostics["length_compaction_reason"] = "compaction_empty"
        return content, diagnostics
    if compacted_word_count < policy.min_words:
        diagnostics["length_compaction_reason"] = "compaction_under_min"
        return content, diagnostics
    if compacted_word_count >= before_word_count:
        diagnostics["length_compaction_reason"] = "compaction_not_shorter"
        return content, diagnostics

    diagnostics["length_compaction_applied"] = True
    diagnostics["length_compaction_reason"] = (
        "hard_ceiling_exceeded"
        if before_word_count > policy.hard_ceiling_words
        else "soft_max_exceeded"
    )
    return compacted, diagnostics


def trim_generated_text(text: str, hard_limit: int) -> str:
    """Trim text to hard_limit chars at the nearest sentence boundary (。！？…… or \\n\\n).
    If no boundary found within last 20% of limit, truncate at limit directly.
    Returns text unchanged if len(text) <= hard_limit.
    """
    if len(text) <= hard_limit:
        return text
    search_start = int(hard_limit * 0.80)
    window = text[search_start:hard_limit]
    best_pos = -1
    for punct in ("。", "！", "？", "……", "\n\n"):
        idx = window.rfind(punct)
        if idx != -1:
            candidate = search_start + idx + len(punct)
            if candidate > best_pos:
                best_pos = candidate
    if best_pos > 0:
        return text[:best_pos]
    return text[:hard_limit]
