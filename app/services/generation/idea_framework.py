"""Idea framework generation service."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from pydantic import BaseModel

from app.core.llm import get_llm_with_fallback
from app.core.strategy import get_model_for_stage
from app.prompts import render_prompt

logger = logging.getLogger(__name__)


class IdeaFrameworkSchema(BaseModel):
    one_liner: str
    premise: str
    conflict: str
    hook: str
    selling_point: str


def detect_title_language(title: str) -> str:
    text = (title or "").strip()
    if not text:
        return "zh"
    if re.search(r"[\u0600-\u06FF]", text):
        return "ar"
    if re.search(r"[\uAC00-\uD7AF]", text):
        return "ko"
    if re.search(r"[\u3040-\u30FF]", text):
        return "ja"
    if re.search(r"[\u4E00-\u9FFF]", text):
        return "zh"
    if re.search(r"[A-Za-z]", text):
        return "en"
    return "zh"


def _extract_json(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    if not raw.startswith("{"):
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if match:
            raw = match.group(0)
    return json.loads(raw)


def _fallback_framework(title: str) -> dict[str, str]:
    t = title.strip() or "未命名故事"
    one_liner = f"{t}：主角在巨变中被迫踏上逆转命运的道路，并在代价与选择中完成蜕变。"
    return {
        "one_liner": one_liner[:180],
        "premise": f"背景设定：围绕《{t}》构建高压世界与明确规则，开篇即抛出危机。",
        "conflict": "核心冲突：主角目标与外部强敌/制度正面对撞，并夹带内在价值冲突。",
        "hook": "章节钩子：每章结尾抛出新的不可逆信息或选择题，推动读者继续追更。",
        "selling_point": "卖点：强节奏推进 + 情绪兑现 + 阶段性反转，保持持续阅读快感。",
    }


def generate_idea_framework(
    title: str,
    language: str | None = None,
    genre: str | None = None,
    style: str | None = None,
    strategy: str | None = None,
) -> dict[str, str]:
    clean_title = (title or "").strip()
    if not clean_title:
        raise ValueError("title is required")
    resolved_language = (language or "").strip() or detect_title_language(clean_title)

    provider, model = get_model_for_stage(strategy or "web-novel", "architect")
    llm = get_llm_with_fallback(provider, model)
    prompt = render_prompt(
        "idea_framework",
        title=clean_title,
        language=resolved_language,
        genre=genre or "",
        style=style or "",
    )
    try:
        resp = llm.invoke(prompt)
        payload = _extract_json(str(resp.content))
        parsed = IdeaFrameworkSchema.model_validate(payload)
        return parsed.model_dump()
    except Exception as exc:
        logger.warning("idea framework generation fallback: %s", exc)
        return _fallback_framework(clean_title)
