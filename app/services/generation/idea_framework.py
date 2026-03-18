"""Idea framework generation service."""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import yaml
from pydantic import BaseModel

from app.core.llm import get_llm_with_fallback, response_to_text
from app.core.strategy import get_model_for_stage
from app.prompts import render_prompt
from app.services.system_settings.runtime import get_primary_chat_runtime

logger = logging.getLogger(__name__)

_PRESETS_DIR = Path(__file__).resolve().parents[3] / "presets"


@lru_cache(maxsize=1)
def _load_genre_options() -> list[dict[str, str]]:
    p = _PRESETS_DIR / "genres.yaml"
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


@lru_cache(maxsize=1)
def _load_style_options() -> list[dict[str, str]]:
    p = _PRESETS_DIR / "styles.yaml"
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


class IdeaFrameworkSchema(BaseModel):
    one_liner: str
    premise: str
    conflict: str
    hook: str
    selling_point: str
    recommended_genre: str | None = None
    recommended_style: str | None = None


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


def _extract_json(text: str) -> dict[str, Any] | str:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        if not raw.startswith("{"):
            match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            if match:
                raw = match.group(0)
        return json.loads(raw)


def _coerce_framework_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, IdeaFrameworkSchema):
        return raw.model_dump()

    from langchain_core.messages import BaseMessage
    if isinstance(raw, BaseMessage):
        text = response_to_text(raw)
        payload = _extract_json(text)
        if isinstance(payload, str):
            payload = _extract_json(payload)
        if isinstance(payload, dict):
            return payload
        raise TypeError("idea framework response must be a JSON object")

    if isinstance(raw, BaseModel):
        dumped = raw.model_dump()
        if isinstance(dumped, dict):
            return dumped
        raise TypeError("idea framework structured output must dump to a dictionary")
    if isinstance(raw, dict):
        return raw

    payload = _extract_json(response_to_text(raw))
    if isinstance(payload, str):
        payload = _extract_json(payload)
    if isinstance(payload, dict):
        return payload
    raise TypeError("idea framework response must be a JSON object")


def _direct_chat_completion(model: str, prompt: str) -> str:
    """Bypass LangChain and call the OpenAI-compatible endpoint directly via httpx.

    Some local LLM servers return responses with non-standard Content-Type headers
    that confuse the OpenAI SDK's response parser (returns raw text instead of a
    ChatCompletion object, causing AttributeError in LangChain). This function
    makes the same request at the HTTP level and extracts the assistant content.
    """
    primary = get_primary_chat_runtime()
    base_url = str(primary.get("base_url") or "").strip().rstrip("/")
    api_key = str(primary.get("api_key") or "")
    url = f"{base_url}/chat/completions" if base_url else "https://api.openai.com/v1/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
    }
    resp = httpx.post(url, json=body, headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


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

    genre_options = _load_genre_options()
    style_options = _load_style_options()
    valid_genre_ids = {g["id"] for g in genre_options}
    valid_style_ids = {s["id"] for s in style_options}

    provider, model = get_model_for_stage(strategy or "web-novel", "architect")
    llm = get_llm_with_fallback(provider, model)
    prompt = render_prompt(
        "idea_framework",
        title=clean_title,
        language=resolved_language,
        genre=genre or "",
        style=style or "",
        genre_options=genre_options,
        style_options=style_options,
    )

    raw_text: str | None = None
    try:
        resp = llm.invoke(prompt)
        payload = _coerce_framework_payload(resp)
    except AttributeError:
        # LangChain / OpenAI SDK failed to parse the response object
        # (common with local OpenAI-compatible servers that return
        # non-standard Content-Type headers). Fall back to direct HTTP.
        logger.info("idea framework: LangChain parse failed, retrying via direct HTTP")
        try:
            raw_text = _direct_chat_completion(model, prompt)
            payload = _extract_json(raw_text)
            if isinstance(payload, str):
                payload = _extract_json(payload)
            if not isinstance(payload, dict):
                raise TypeError("direct call response is not a JSON object")
        except Exception as inner:
            logger.warning("idea framework direct HTTP fallback failed: %s", inner)
            return _fallback_framework(clean_title)
    except Exception as exc:
        logger.warning("idea framework generation fallback: %s", exc)
        return _fallback_framework(clean_title)

    try:
        parsed = IdeaFrameworkSchema.model_validate(payload)
        result = parsed.model_dump()
        if genre:
            result["recommended_genre"] = genre
        elif result.get("recommended_genre") not in valid_genre_ids:
            result["recommended_genre"] = None
        if style:
            result["recommended_style"] = style
        elif result.get("recommended_style") not in valid_style_ids:
            result["recommended_style"] = None
        return result
    except Exception as exc:
        logger.warning("idea framework validation fallback: %s", exc)
        return _fallback_framework(clean_title)
