"""Centralized token usage tracking for all LLM calls."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


def _parse_stage_prefix(stage: str | None) -> tuple[str | None, str | None]:
    """解析 ``llm.{provider}.{model}`` → (provider, model)。

    ``model`` 可能本身含 ``.`` （如 ``gemini-1.5-flash`` ），所以 split 限制 maxsplit=2。
    """
    if not stage:
        return None, None
    parts = str(stage).split(".", 2)
    if len(parts) >= 3 and parts[0] == "llm":
        return parts[1] or None, parts[2] or None
    return None, None


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


# 记录"自上次 pop 起累积发生的所有 LLM 调用"。这是一条 *list*，因为同一个
# agent 内通常有：
#   - structured-output 解析失败的重试（不止一次 LLM）
#   - circuit breaker fallback 链 primary → fallback_a → fallback_b
#   - 同一 stage 多轮 review
# 旧实现是单值 ContextVar，后到的调用会把前一次覆盖，导致 cost 严重低估。
# 现在每次 ``record_usage_from_response`` 都 append；``pop_last_llm_call()``
# 返回整段并清空。``emit_agent_event`` 会把所有 cost 累加进 payload + Prom。
_last_llm_call_var: ContextVar[list[dict[str, Any]] | None] = ContextVar(
    "llm_last_calls", default=None
)


def pop_last_llm_call() -> list[dict[str, Any]]:
    """取出并清空自上次 pop 起累积的全部 LLM 调用元数据。

    返回值固定是 list（可能为空），调用方按时间序遍历计算 cost / token。
    """
    snapshot = _last_llm_call_var.get() or []
    _last_llm_call_var.set(None)
    if not isinstance(snapshot, list):
        return [snapshot] if snapshot else []
    return list(snapshot)


def peek_last_llm_call() -> dict[str, Any] | None:
    """只读最近一次调用元数据，不清空（debug/metric 用）。返回末条或 ``None``。"""
    bucket = _last_llm_call_var.get()
    if not bucket:
        return None
    if isinstance(bucket, list):
        return bucket[-1] if bucket else None
    return bucket


def _append_llm_call(record: dict[str, Any]) -> None:
    """内部：追加一条 LLM 调用记录到当前 ContextVar 桶。"""
    bucket = _last_llm_call_var.get()
    if not isinstance(bucket, list):
        # 兼容旧的单值（极少分支，但避免历史调用方崩）
        bucket = [bucket] if isinstance(bucket, dict) else []
    bucket = list(bucket)
    bucket.append(record)
    _last_llm_call_var.set(bucket)


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

    # 记录单次调用元数据 + cost_usd（供 emit_agent_event 自动消费）
    provider, model = _parse_stage_prefix(stage)
    cost_usd = 0.0
    try:
        from app.services.cost.budget import compute_cost

        cost_usd = float(compute_cost(model or "", in_t, out_t)) if model else 0.0
    except Exception:
        cost_usd = 0.0
    _append_llm_call(
        {
            "provider": provider,
            "model": model,
            "stage": stage or "",
            "input_tokens": in_t,
            "output_tokens": out_t,
            "total_tokens": total_t,
            "cost_usd": cost_usd,
        }
    )
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
