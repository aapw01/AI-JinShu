"""Volume brief distill (#3a).

把一卷内若干章节摘要压缩成 ≤ N 字的"分卷概要"，落到
``story_snapshots.snapshot_json["volume_brief"]``。

flag ``memory.volume_brief_distill`` 关闭时直接返回 None（不调用 LLM、不写表）。
"""

from __future__ import annotations

import logging
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.feature_flags import is_enabled
from app.core.llm import get_llm
from app.models.novel import StorySnapshot
from app.prompts import render_prompt
from app.services.agents.events import emit_agent_event

logger = logging.getLogger(__name__)


_MAX_CHARS = 600


class VolumeBrief(BaseModel):
    """三段式 volume brief。

    schema_version=2 起拆为 ``characters/conflicts/foreshadowings``；老数据
    仍保留 ``text`` 字段以便平滑过渡，新数据 ``text`` 为三段拼接。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=2)
    volume_no: int
    chapter_from: int
    chapter_to: int
    text: str
    char_count: int
    characters: str = ""
    conflicts: str = ""
    foreshadowings: str = ""


def distill_volume_brief(
    db: Session,
    *,
    novel_id: int,
    novel_version_id: int | None,
    volume_no: int,
    chapter_summaries: Iterable[dict],
    max_chars: int = _MAX_CHARS,
) -> VolumeBrief | None:
    """LLM 调用 + 持久化。失败 → None。"""
    if not is_enabled("memory.volume_brief_distill", novel_id=novel_id):
        return None
    summaries = [s for s in (chapter_summaries or []) if isinstance(s, dict) and s.get("summary")]
    if not summaries:
        return None
    chapter_from = min(int(s.get("chapter_num") or 0) for s in summaries)
    chapter_to = max(int(s.get("chapter_num") or 0) for s in summaries)

    try:
        prompt = render_prompt(
            "volume_brief_distill",
            volume_no=volume_no,
            chapter_from=chapter_from,
            chapter_to=chapter_to,
            chapter_summaries=summaries,
            max_chars=max_chars,
        )
    except Exception:
        logger.exception("volume_brief: render_prompt failed")
        return None

    try:
        llm = get_llm()
        resp = llm.invoke(prompt)
    except Exception as exc:
        logger.exception("volume_brief: llm.invoke failed")
        emit_agent_event(
            agent_name="volume_brief_distiller",
            event_type="distill",
            novel_id=int(novel_id),
            novel_version_id=novel_version_id,
            verdict="fail",
            error_code="LLM_INVOKE_FAILED",
            error_category="transient",
            payload={"volume_no": volume_no, "error": str(exc)},
        )
        return None

    raw = (resp.content if hasattr(resp, "content") else str(resp)).strip()
    if not raw:
        return None

    # 解析三段式 JSON；失败 fallback 到把整段当 text（schema_version=1 兼容）
    sections = _parse_three_sections(raw)
    used_fallback = sections is None
    if used_fallback:
        if len(raw) > max_chars * 2:
            raw = raw[: max_chars * 2]
        brief = VolumeBrief(
            schema_version=1,
            volume_no=volume_no,
            chapter_from=chapter_from,
            chapter_to=chapter_to,
            text=raw,
            char_count=len(raw),
        )
        # 告诉下游"这是兜底产物，结构化字段缺失，按 schema_v1 解析"
        emit_agent_event(
            agent_name="volume_brief_distiller",
            event_type="schema_v1_fallback",
            novel_id=int(novel_id),
            novel_version_id=novel_version_id,
            verdict="warn",
            error_code="VOLUME_BRIEF_PARSE_FAILED",
            error_category="transient",
            payload={
                "volume_no": volume_no,
                "raw_len": len(raw),
                "preview": raw[:120],
            },
        )
    else:
        characters = sections.get("characters", "")
        conflicts = sections.get("conflicts", "")
        foreshadowings = sections.get("foreshadowings", "")
        text = "\n".join(
            x for x in [characters, conflicts, foreshadowings] if x
        )
        if len(text) > max_chars * 2:
            text = text[: max_chars * 2]
        brief = VolumeBrief(
            schema_version=2,
            volume_no=volume_no,
            chapter_from=chapter_from,
            chapter_to=chapter_to,
            text=text,
            char_count=len(text),
            characters=characters,
            conflicts=conflicts,
            foreshadowings=foreshadowings,
        )

    if novel_version_id is not None:
        existing = db.execute(
            select(StorySnapshot)
            .where(StorySnapshot.novel_version_id == novel_version_id)
            .where(StorySnapshot.volume_no == volume_no)
        ).scalar_one_or_none()
        snapshot_json = dict(existing.snapshot_json or {}) if existing else {}
        snapshot_json["volume_brief"] = brief.model_dump()
        if existing is None:
            db.add(
                StorySnapshot(
                    novel_id=novel_id,
                    novel_version_id=novel_version_id,
                    volume_no=volume_no,
                    chapter_end=chapter_to,
                    snapshot_json=snapshot_json,
                )
            )
        else:
            existing.snapshot_json = snapshot_json
            existing.chapter_end = max(existing.chapter_end or 0, chapter_to)
        try:
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("volume_brief: persist failed")
            return None

    emit_agent_event(
        agent_name="volume_brief_distiller",
        event_type="distill",
        novel_id=int(novel_id),
        novel_version_id=novel_version_id,
        verdict="pass" if not used_fallback else "warn",
        payload={
            "volume_no": volume_no,
            "char_count": brief.char_count,
            "chapter_from": chapter_from,
            "chapter_to": chapter_to,
            "schema_version": brief.schema_version,
            "used_fallback": used_fallback,
        },
    )
    return brief


def _parse_three_sections(raw: str) -> dict[str, str] | None:
    """从 LLM 响应里抠 ``{characters, conflicts, foreshadowings}`` JSON。

    支持：
    - 纯 JSON
    - 用 ```json``` 包裹的 fenced JSON
    - 反引号包裹的整段 JSON

    任何失败返回 ``None``，调用方走旧 fallback 路径。
    """
    import json
    import re

    text = raw.strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
        if m:
            text = m.group(1)
    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except Exception:
            return None
    if not isinstance(data, dict):
        return None
    chars_v = str(data.get("characters") or "").strip()
    conflicts_v = str(data.get("conflicts") or "").strip()
    foreshadow_v = str(data.get("foreshadowings") or "").strip()
    if not (chars_v or conflicts_v or foreshadow_v):
        return None
    return {
        "characters": chars_v,
        "conflicts": conflicts_v,
        "foreshadowings": foreshadow_v,
    }


__all__ = ["VolumeBrief", "distill_volume_brief"]
