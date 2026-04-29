"""Centralized token usage tracking for all LLM calls."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


def _to_int(value: Any) -> int:
    """执行 to int 相关辅助逻辑。"""
    try:
        return int(value or 0)
    except Exception:
        return 0


@dataclass
class UsageSession:
    """保存一次 LLM 用量会话的累计统计结果。"""

    session_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    billable_total_tokens: int = 0
    calls: int = 0
    embedding_calls: int = 0
    stages: dict[str, dict[str, int]] = field(default_factory=dict)
    lock: Any = field(default_factory=RLock, repr=False, compare=False)


_usage_session_var: ContextVar[UsageSession | None] = ContextVar(
    "llm_usage_session", default=None
)


def begin_usage_session(
    session_id: str,
    *,
    base_input: int = 0,
    base_output: int = 0,
    base_billable: int | None = None,
) -> None:
    """Start a new usage session with optional cumulative resume baselines."""
    session = UsageSession(session_id=session_id)
    session.input_tokens = max(0, int(base_input or 0))
    session.output_tokens = max(0, int(base_output or 0))
    session.total_tokens = session.input_tokens + session.output_tokens
    session.billable_total_tokens = max(
        session.total_tokens,
        max(0, int(base_billable or 0)) if base_billable is not None else 0,
    )
    _usage_session_var.set(session)


def end_usage_session() -> dict[str, Any]:
    """执行 end usage session 相关辅助逻辑。"""
    session = _usage_session_var.get()
    _usage_session_var.set(None)
    if not session:
        return {
            "session_id": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "billable_total_tokens": 0,
            "calls": 0,
            "embedding_calls": 0,
            "estimated_cost": 0.0,
            "stages": {},
        }
    with session.lock:
        input_tokens = int(session.input_tokens)
        output_tokens = int(session.output_tokens)
        total_tokens = int(session.total_tokens)
        billable_total_tokens = int(session.billable_total_tokens)
        calls = int(session.calls)
        embedding_calls = int(session.embedding_calls)
        stages = {key: dict(value) for key, value in session.stages.items()}
    return {
        "session_id": session.session_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "billable_total_tokens": billable_total_tokens,
        "calls": calls,
        "embedding_calls": embedding_calls,
        # Note: includes base tokens from prior runs — this is cumulative lifetime cost.
        "estimated_cost": estimate_cost(
            input_tokens, output_tokens, billable_total_tokens
        ),
        "stages": stages,
    }


def snapshot_usage() -> dict[str, Any]:
    """执行 snapshot usage 相关辅助逻辑。"""
    session = _usage_session_var.get()
    if not session:
        return {
            "session_id": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "billable_total_tokens": 0,
            "calls": 0,
            "embedding_calls": 0,
            "estimated_cost": 0.0,
            "stages": {},
        }
    with session.lock:
        input_tokens = int(session.input_tokens)
        output_tokens = int(session.output_tokens)
        total_tokens = int(session.total_tokens)
        billable_total_tokens = int(session.billable_total_tokens)
        calls = int(session.calls)
        embedding_calls = int(session.embedding_calls)
        stages = {key: dict(value) for key, value in session.stages.items()}
    return {
        "session_id": session.session_id,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "billable_total_tokens": billable_total_tokens,
        "calls": calls,
        "embedding_calls": embedding_calls,
        "estimated_cost": estimate_cost(
            input_tokens, output_tokens, billable_total_tokens
        ),
        "stages": stages,
    }


def estimate_cost(
    input_tokens: int, output_tokens: int, billable_total_tokens: int | None = None
) -> float:
    """执行 estimate cost 相关辅助逻辑。"""
    billable_total = max(0, int(billable_total_tokens or 0))
    billed_output_tokens = max(0, int(output_tokens))
    if billable_total > 0:
        billed_output_tokens = max(
            billed_output_tokens,
            billable_total - max(0, int(input_tokens)),
        )
    return round(
        (max(0, int(input_tokens)) / 1000) * 0.0015
        + (billed_output_tokens / 1000) * 0.002,
        6,
    )


def _extract_reasoning_tokens(usage: dict[str, Any]) -> int:
    candidates = [
        usage.get("reasoning_tokens"),
        usage.get("thinking_tokens"),
        usage.get("reasoning"),
        usage.get("thinking"),
    ]
    for details_key in (
        "completion_tokens_details",
        "output_token_details",
        "output_tokens_details",
    ):
        details = usage.get(details_key)
        if isinstance(details, dict):
            candidates.extend(
                [
                    details.get("reasoning_tokens"),
                    details.get("thinking_tokens"),
                    details.get("reasoning"),
                    details.get("thinking"),
                ]
            )
    return max((_to_int(value) for value in candidates), default=0)


def _usage_tuple_from_dict(usage: dict[str, Any]) -> tuple[int, int, int, int]:
    in_t = _to_int(usage.get("input_tokens") or usage.get("prompt_tokens"))
    out_t = _to_int(usage.get("output_tokens") or usage.get("completion_tokens"))
    hidden_t = _extract_reasoning_tokens(usage)
    visible_total = in_t + out_t
    total_t = _to_int(usage.get("total_tokens"))
    billable_t = max(total_t, visible_total, visible_total + hidden_t)
    if total_t <= 0:
        total_t = billable_t
    return in_t, out_t, total_t, billable_t


def _extract_usage(response: Any) -> tuple[int, int, int, int]:
    """提取用量。"""
    usage = getattr(response, "usage_metadata", None) or {}
    if isinstance(usage, dict):
        in_t, out_t, total_t, billable_t = _usage_tuple_from_dict(usage)
        if in_t > 0 or out_t > 0 or total_t > 0 or billable_t > 0:
            return in_t, out_t, total_t, billable_t

    meta = getattr(response, "response_metadata", None) or {}
    if isinstance(meta, dict):
        token_usage = (
            meta.get("token_usage")
            if isinstance(meta.get("token_usage"), dict)
            else None
        )
        if token_usage:
            return _usage_tuple_from_dict(token_usage)
        usage2 = meta.get("usage") if isinstance(meta.get("usage"), dict) else None
        if usage2:
            return _usage_tuple_from_dict(usage2)

    return 0, 0, 0, 0


def record_usage_from_response(
    response: Any, *, stage: str | None = None
) -> dict[str, int]:
    """记录用量来源响应。"""
    session = _usage_session_var.get()
    in_t, out_t, total_t, billable_t = _extract_usage(response)
    payload = {
        "input_tokens": in_t,
        "output_tokens": out_t,
        "total_tokens": total_t,
        "billable_total_tokens": billable_t,
    }
    if not session:
        return payload
    total_delta = total_t if total_t > 0 else (in_t + out_t)
    billable_delta = max(billable_t, total_delta)
    with session.lock:
        session.input_tokens += in_t
        session.output_tokens += out_t
        session.total_tokens += total_delta
        session.billable_total_tokens += billable_delta
        session.calls += 1
        if stage:
            bucket = session.stages.setdefault(
                str(stage),
                {
                    "calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                    "billable_total_tokens": 0,
                },
            )
            bucket["calls"] += 1
            bucket["input_tokens"] += in_t
            bucket["output_tokens"] += out_t
            bucket["total_tokens"] += total_delta
            bucket["billable_total_tokens"] += billable_delta
    return payload


def record_embedding_call() -> None:
    """Record one embedding API call. Token counts for embeddings are not
    extractable from LangChain's OpenAIEmbeddings response, so only call count
    is tracked here."""
    session = _usage_session_var.get()
    if session:
        with session.lock:
            session.embedding_calls += 1
