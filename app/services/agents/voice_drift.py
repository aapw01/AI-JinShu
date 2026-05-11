"""Voice drift auditor (#5)."""

from __future__ import annotations

import logging
import re
from typing import Iterable

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.feature_flags import is_enabled
from app.core.gates import get_gate
from app.models.novel import VoiceFingerprintRow
from app.services.agents.contracts.voice import VoiceDriftReport, VoiceFingerprint
from app.services.agents.llm_agent import run_llm_agent

logger = logging.getLogger(__name__)


_DIALOGUE_RE = re.compile(r"[“\"]([^”\"]+)[”\"]")
_SENTENCE_SPLIT = re.compile(r"[。！？!?]")


def extract_dialogues(text: str) -> list[str]:
    if not text:
        return []
    return [m.group(1).strip() for m in _DIALOGUE_RE.finditer(text) if m.group(1).strip()]


def compute_statistical_fingerprint(dialogues: Iterable[str]) -> dict:
    sents: list[str] = []
    for d in dialogues:
        sents.extend([s.strip() for s in _SENTENCE_SPLIT.split(d) if s.strip()])
    if not sents:
        return {"avg_sentence_len": 0.0, "formality_score": 0.5, "voice_register": "neutral"}
    avg = sum(len(s) for s in sents) / len(sents)
    formal_markers = sum(1 for s in sents if any(k in s for k in ("阁下", "在下", "敬", "恭", "先生", "请")))
    casual_markers = sum(1 for s in sents if any(k in s for k in ("呗", "呐", "嘛", "啦", "啊", "靠")))
    formality_raw = formal_markers / max(1, len(sents)) - casual_markers / max(1, len(sents))
    formality_score = max(0.0, min(1.0, 0.5 + formality_raw))
    if formality_score >= 0.65:
        register = "high"
    elif formality_score <= 0.35:
        register = "low"
    else:
        register = "neutral"
    return {
        "avg_sentence_len": avg,
        "formality_score": formality_score,
        "voice_register": register,
    }


class _LLMVoiceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drift_score: float = Field(ge=0.0, le=1.0)
    triggered: bool
    diff_dimensions: list[str] = Field(default_factory=list)


def audit_chapter_voice(
    db: Session,
    *,
    novel_id: int,
    novel_version_id: int | None,
    chapter_num: int,
    character_key: str,
    chapter_text: str,
) -> VoiceDriftReport | None:
    """flag-controlled：用历史 fingerprint + LLM 评估 drift。返回 None 表示
    flag 关闭 / LLM 不可用 / 无对白片段。
    """
    if not is_enabled("quality.voice_drift_audit", novel_id=novel_id):
        return None
    dialogues = extract_dialogues(chapter_text)
    if not dialogues:
        return None

    historical = None
    if novel_version_id is not None:
        historical = db.execute(
            select(VoiceFingerprintRow)
            .where(VoiceFingerprintRow.novel_version_id == novel_version_id)
            .where(VoiceFingerprintRow.character_key == character_key)
        ).scalar_one_or_none()
    if historical is None:
        # 无历史指纹 → 用当前章节统计建立基线，不报告 drift。
        stats = compute_statistical_fingerprint(dialogues)
        if novel_version_id is not None:
            db.add(
                VoiceFingerprintRow(
                    novel_version_id=novel_version_id,
                    character_key=character_key,
                    avg_sentence_len=stats["avg_sentence_len"],
                    formality_score=stats["formality_score"],
                    register=stats["voice_register"],
                    sample_chapter_from=chapter_num,
                    sample_chapter_to=chapter_num,
                    payload=stats,
                )
            )
            try:
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("voice_drift: baseline persist failed")
        return None

    gate = get_gate("voice_drift", "drift_threshold", novel_id=novel_id)
    threshold = float(gate.threshold or 0.35)
    history_fp = VoiceFingerprint(
        character_key=character_key,
        avg_sentence_len=float(historical.avg_sentence_len or 0.0),
        formality_score=float(historical.formality_score or 0.5),
        voice_register=str(historical.register or "neutral"),  # type: ignore[arg-type]
    )

    parsed = run_llm_agent(
        agent_name="voice_drift",
        event_type="audit",
        template="voice_drift_audit",
        template_kwargs={
            "historical_fingerprint": history_fp.model_dump(),
            "current_dialogues": dialogues,
            "threshold": threshold,
            "character_key": character_key,
        },
        schema=_LLMVoiceOutput,
        novel_id=novel_id,
        chapter_num=chapter_num,
        novel_version_id=novel_version_id,
    )
    if parsed is None:
        return None

    report = VoiceDriftReport(
        character_key=character_key,
        drift_score=parsed.drift_score,
        threshold=threshold,
        triggered=bool(parsed.triggered),
        diff_dimensions=list(parsed.diff_dimensions or []),
    )
    try:
        from app.core.metrics import voice_drift_score, voice_drift_warnings_total

        voice_drift_score.set(parsed.drift_score, size_bucket="default")
        if report.triggered:
            voice_drift_warnings_total.inc()
    except Exception:
        pass
    return report


__all__ = [
    "VoiceDriftReport",
    "audit_chapter_voice",
    "compute_statistical_fingerprint",
    "extract_dialogues",
]
