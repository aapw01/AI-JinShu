"""Centralized token usage tracking for all LLM calls."""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any


def _to_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


@dataclass
class UsageSession:
    session_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0
    stages: dict[str, dict[str, int]] = field(default_factory=dict)


_usage_session_var: ContextVar[UsageSession | None] = ContextVar("llm_usage_session", default=None)


def begin_usage_session(session_id: str) -> None:
    _usage_session_var.set(UsageSession(session_id=session_id))


def end_usage_session() -> dict[str, Any]:
    session = _usage_session_var.get()
    _usage_session_var.set(None)
    if not session:
        return {
            "session_id": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
            "estimated_cost": 0.0,
            "stages": {},
        }
    return {
        "session_id": session.session_id,
        "input_tokens": int(session.input_tokens),
        "output_tokens": int(session.output_tokens),
        "total_tokens": int(session.total_tokens),
        "calls": int(session.calls),
        "estimated_cost": estimate_cost(session.input_tokens, session.output_tokens),
        "stages": session.stages,
    }


def snapshot_usage() -> dict[str, Any]:
    session = _usage_session_var.get()
    if not session:
        return {
            "session_id": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "calls": 0,
            "estimated_cost": 0.0,
            "stages": {},
        }
    return {
        "session_id": session.session_id,
        "input_tokens": int(session.input_tokens),
        "output_tokens": int(session.output_tokens),
        "total_tokens": int(session.total_tokens),
        "calls": int(session.calls),
        "estimated_cost": estimate_cost(session.input_tokens, session.output_tokens),
        "stages": dict(session.stages),
    }


def estimate_cost(input_tokens: int, output_tokens: int) -> float:
    return round((max(0, int(input_tokens)) / 1000) * 0.0015 + (max(0, int(output_tokens)) / 1000) * 0.002, 6)


def _extract_usage(response: Any) -> tuple[int, int, int]:
    usage = getattr(response, "usage_metadata", None) or {}
    if isinstance(usage, dict):
        in_t = _to_int(usage.get("input_tokens") or usage.get("prompt_tokens"))
        out_t = _to_int(usage.get("output_tokens") or usage.get("completion_tokens"))
        total_t = _to_int(usage.get("total_tokens"))
        if total_t <= 0:
            total_t = in_t + out_t
        if in_t > 0 or out_t > 0 or total_t > 0:
            return in_t, out_t, total_t

    meta = getattr(response, "response_metadata", None) or {}
    if isinstance(meta, dict):
        token_usage = meta.get("token_usage") if isinstance(meta.get("token_usage"), dict) else None
        if token_usage:
            in_t = _to_int(token_usage.get("prompt_tokens") or token_usage.get("input_tokens"))
            out_t = _to_int(token_usage.get("completion_tokens") or token_usage.get("output_tokens"))
            total_t = _to_int(token_usage.get("total_tokens"))
            if total_t <= 0:
                total_t = in_t + out_t
            return in_t, out_t, total_t
        usage2 = meta.get("usage") if isinstance(meta.get("usage"), dict) else None
        if usage2:
            in_t = _to_int(usage2.get("input_tokens") or usage2.get("prompt_tokens"))
            out_t = _to_int(usage2.get("output_tokens") or usage2.get("completion_tokens"))
            total_t = _to_int(usage2.get("total_tokens"))
            if total_t <= 0:
                total_t = in_t + out_t
            return in_t, out_t, total_t

    return 0, 0, 0


def record_usage_from_response(response: Any, *, stage: str | None = None) -> dict[str, int]:
    session = _usage_session_var.get()
    in_t, out_t, total_t = _extract_usage(response)
    if not session:
        return {"input_tokens": in_t, "output_tokens": out_t, "total_tokens": total_t}
    session.input_tokens += in_t
    session.output_tokens += out_t
    session.total_tokens += total_t if total_t > 0 else (in_t + out_t)
    session.calls += 1
    if stage:
        bucket = session.stages.setdefault(
            str(stage),
            {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )
        bucket["calls"] += 1
        bucket["input_tokens"] += in_t
        bucket["output_tokens"] += out_t
        bucket["total_tokens"] += total_t if total_t > 0 else (in_t + out_t)
    return {"input_tokens": in_t, "output_tokens": out_t, "total_tokens": total_t}

