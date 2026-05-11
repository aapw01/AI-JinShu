"""Unified agent event emitter (Phase 0 §4.1 / §4.3.1).

设计要点：

- **swallow first**：emit 任何失败都不能阻塞主路径；写日志即可。
- **trace_id 自动继承**：调用方不传时从 ``app.core.trace.get_trace_id`` 上下文取。
- **Prometheus 桥接软依赖**：``prometheus_client`` 不在仓库依赖里，缺失时 fallback
  到进程内计数器，便于测试断言；后续真正接入 Prom 时只需安装包即可生效。
- **payload 注册表 stub**：每个 ``(agent_name, event_type)`` 组合可挂一个
  Pydantic 模型；本 PR 仅搭骨架，注册表为空时直接 pass through，并在调试
  日志里提示该组合未注册（不阻塞），改造点 PR 自行补注册。
- **基数控制**：``novel_id`` / ``task_id`` / ``chapter_num`` 等高基数维度只入
  ``agent_events`` 表，绝对不进 Prometheus label（见 §4.1 / 附录 C）。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Mapping

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.llm_usage import pop_last_llm_call
from app.core.metrics import (
    agent_token_cost_total,
    agent_token_input_total,
    agent_token_output_total,
)
from app.core.trace import get_trace_id
from app.models.novel import AgentEvent

logger = logging.getLogger(__name__)


# --- Prometheus / fallback counter --------------------------------------------------

try:  # pragma: no cover - exercised via _PromAvailable below
    from prometheus_client import Counter as _PromCounter  # type: ignore[import-not-found]
except Exception:  # ImportError or any other failure during prom import
    _PromCounter = None  # type: ignore[assignment]


_FALLBACK_COUNTER_LOCK = threading.Lock()
_FALLBACK_COUNTERS: dict[tuple[str, str, str], int] = {}


if _PromCounter is not None:  # pragma: no cover - depends on optional dep
    _AGENT_EVENTS_TOTAL = _PromCounter(
        "agent_events_total",
        "Count of agent events emitted, partitioned by agent / event_type / verdict.",
        labelnames=("agent", "event_type", "verdict"),
    )
else:
    _AGENT_EVENTS_TOTAL = None


def _bump_counter(agent: str, event_type: str, verdict: str) -> None:
    """同时打 Prometheus（如可用）和进程内 fallback 计数器。"""
    if _AGENT_EVENTS_TOTAL is not None:  # pragma: no cover - optional path
        try:
            _AGENT_EVENTS_TOTAL.labels(agent=agent, event_type=event_type, verdict=verdict).inc()
        except Exception:
            logger.debug("prometheus counter inc failed", exc_info=True)
    key = (agent, event_type, verdict)
    with _FALLBACK_COUNTER_LOCK:
        _FALLBACK_COUNTERS[key] = _FALLBACK_COUNTERS.get(key, 0) + 1


def get_fallback_counter(agent: str, event_type: str, verdict: str) -> int:
    """读取进程内计数器，仅供测试断言使用。生产用 Prometheus 抓 ``agent_events_total``。"""
    with _FALLBACK_COUNTER_LOCK:
        return _FALLBACK_COUNTERS.get((agent, event_type, verdict), 0)


def reset_fallback_counters() -> None:
    """清空进程内 fallback 计数器，仅供测试 setup 使用。"""
    with _FALLBACK_COUNTER_LOCK:
        _FALLBACK_COUNTERS.clear()


# --- Payload registry stub (§4.3.1) -------------------------------------------------
#
# 真实的契约会在每条改造各自的 PR 里挂上 Pydantic 模型。Phase 0 只搭注册和
# 校验入口，避免后续每个改造重复造轮子；同时允许空注册时 pass through。
#
# 调用约束：
#   register_event_payload(("consistency_check", "revise_attempt"))(ReviseAttemptPayload)
# 校验时若注册表里没有匹配项，记录一条 debug 日志后放行；如果有但校验失败，
# emit 仍写库（payload 为原 dict），但额外 emit 一条 schema_violation 元事件。

_EVENT_PAYLOAD_REGISTRY: dict[tuple[str, str], Any] = {}


def register_event_payload(key: tuple[str, str]):
    """装饰器：把 ``(agent_name, event_type)`` 与 Pydantic 模型绑定。"""

    def _wrap(model_cls):
        _EVENT_PAYLOAD_REGISTRY[key] = model_cls
        return model_cls

    return _wrap


def get_event_payload_schema(agent_name: str, event_type: str):
    """读取注册的 payload schema。未注册返回 ``None``。"""
    return _EVENT_PAYLOAD_REGISTRY.get((agent_name, event_type))


def _validate_payload(
    agent_name: str, event_type: str, payload: Mapping[str, Any] | None
) -> tuple[dict[str, Any], str | None]:
    """返回 ``(normalized_payload, schema_error)``。

    - 注册表无匹配：放行（debug 日志），无错误。
    - 注册了 schema：``model_validate``，失败时返回错误信息（仍放行原 payload）。
    """
    raw: dict[str, Any] = dict(payload or {})
    schema = get_event_payload_schema(agent_name, event_type)
    if schema is None:
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "agent_event payload schema not registered",
                extra={"agent": agent_name, "event_type": event_type},
            )
        return raw, None
    try:
        validated = schema.model_validate(raw)  # type: ignore[attr-defined]
    except Exception as exc:  # pydantic ValidationError or anything else
        return raw, f"{type(exc).__name__}: {exc}"
    try:
        return validated.model_dump(mode="json"), None  # type: ignore[attr-defined]
    except Exception:
        return raw, None


# --- Public emit API ---------------------------------------------------------------

_VALID_EVENT_CATEGORIES = {"transient", "permanent", "policy"}


def _resolve_model_tier(model: str) -> str:
    """通过 ``model_prices.yaml`` 反查 model 所属 tier。失败返回 ``unknown``。"""
    if not model:
        return "unknown"
    try:
        from app.services.cost.budget import _load_prices  # type: ignore[attr-defined]

        prices = _load_prices()
        if prices is None:
            return "unknown"
        price = prices.models.get(model.strip(), prices.fallback_unknown_model)
        return getattr(price, "tier", "unknown") or "unknown"
    except Exception:
        return "unknown"


def emit_agent_event(
    *,
    agent_name: str,
    event_type: str,
    novel_id: int,
    chapter_num: int | None = None,
    novel_version_id: int | None = None,
    task_id: str | None = None,
    trace_id: str | None = None,
    verdict: str | None = None,
    error_code: str | None = None,
    error_category: str | None = None,
    duration_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    payload: Mapping[str, Any] | None = None,
    db: Session | None = None,
) -> None:
    """统一事件入口（§4.1）。

    Args 详见 ``app/models/novel.py::AgentEvent`` 字段。``trace_id`` 不传时从
    上下文继承（``app.core.trace.set_trace_id`` 设置过的值）。``db`` 不传时
    自行打开/关闭一个 ``SessionLocal``。

    失败必须 swallow（写日志），绝不抛异常给主路径。
    """
    try:
        if not agent_name or not event_type:
            logger.warning(
                "emit_agent_event called without agent_name/event_type",
                extra={"agent": agent_name, "event_type": event_type},
            )
            return
        if error_category is not None and error_category not in _VALID_EVENT_CATEGORIES:
            logger.warning(
                "emit_agent_event got unknown error_category",
                extra={"error_category": error_category},
            )

        resolved_trace = trace_id if trace_id is not None else get_trace_id()

        # 自动消费 *本次 emit 之前累积的所有 LLM 调用*（来自同一 ContextVar
        # 桶）：把 model / input_tokens / output_tokens / cost_usd 注入 payload
        # + 同步参数。调用方显式传入的值优先生效（不被覆盖）。
        try:
            calls = pop_last_llm_call() or []
        except Exception:
            calls = []
        if not isinstance(calls, list):
            calls = [calls] if calls else []

        merged_payload: dict[str, Any] = dict(payload or {})
        if calls:
            total_cost = 0.0
            total_in = 0
            total_out = 0
            for c in calls:
                if not isinstance(c, dict):
                    continue
                total_cost += float(c.get("cost_usd") or 0.0)
                total_in += int(c.get("input_tokens") or 0)
                total_out += int(c.get("output_tokens") or 0)
            # 末条调用代表"实际产出本次 emit 的模型"——给 payload 留快照便于排查
            last = calls[-1] if isinstance(calls[-1], dict) else {}
            if input_tokens is None and total_in > 0:
                input_tokens = total_in
            if output_tokens is None and total_out > 0:
                output_tokens = total_out
            merged_payload.setdefault("model", last.get("model"))
            merged_payload.setdefault("provider", last.get("provider"))
            merged_payload.setdefault("cost_usd", round(total_cost, 6))
            if len(calls) > 1:
                # 多次 LLM（重试/级联/多 stage）时附详情，便于 cost dashboard 下钻
                merged_payload.setdefault(
                    "llm_calls",
                    [
                        {
                            "stage": c.get("stage"),
                            "model": c.get("model"),
                            "provider": c.get("provider"),
                            "input_tokens": int(c.get("input_tokens") or 0),
                            "output_tokens": int(c.get("output_tokens") or 0),
                            "cost_usd": round(float(c.get("cost_usd") or 0.0), 6),
                        }
                        for c in calls
                        if isinstance(c, dict)
                    ],
                )

            # 累加每条 LLM 调用的真实 cost / token —— 必须按条累加，否则
            # cost dashboard 只能告诉你"emit 了几次"，无法做 USD 维度告警。
            try:
                for c in calls:
                    if not isinstance(c, dict):
                        continue
                    stage = str(c.get("stage") or "")
                    tier = _resolve_model_tier(str(c.get("model") or ""))
                    cost = float(c.get("cost_usd") or 0.0)
                    in_t = int(c.get("input_tokens") or 0)
                    out_t = int(c.get("output_tokens") or 0)
                    if cost > 0:
                        agent_token_cost_total.inc(
                            cost, agent=agent_name, stage=stage, model_tier=tier
                        )
                    if in_t > 0:
                        agent_token_input_total.inc(
                            in_t, agent=agent_name, stage=stage
                        )
                    if out_t > 0:
                        agent_token_output_total.inc(
                            out_t, agent=agent_name, stage=stage
                        )
            except Exception:
                logger.debug("emit_agent_event metric bridge failed", exc_info=True)

        normalized_payload, schema_error = _validate_payload(
            agent_name, event_type, merged_payload
        )

        owns_session = db is None
        session = db or SessionLocal()
        try:
            row = AgentEvent(
                trace_id=resolved_trace,
                novel_id=novel_id,
                novel_version_id=novel_version_id,
                task_id=task_id,
                chapter_num=chapter_num,
                agent_name=agent_name,
                event_type=event_type,
                verdict=verdict,
                error_code=error_code,
                error_category=error_category,
                duration_ms=duration_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                payload=normalized_payload,
            )
            session.add(row)
            if owns_session:
                session.commit()
            else:
                session.flush()
        except Exception:
            logger.exception(
                "emit_agent_event failed to persist",
                extra={"agent": agent_name, "event_type": event_type},
            )
            if owns_session:
                try:
                    session.rollback()
                except Exception:
                    logger.debug("emit_agent_event rollback failed", exc_info=True)
            return
        finally:
            if owns_session:
                try:
                    session.close()
                except Exception:
                    logger.debug("emit_agent_event session close failed", exc_info=True)

        _bump_counter(agent_name, event_type, verdict or "")

        if schema_error is not None:
            try:
                _emit_schema_violation_meta(
                    agent_name=agent_name,
                    event_type=event_type,
                    novel_id=novel_id,
                    chapter_num=chapter_num,
                    trace_id=resolved_trace,
                    schema_error=schema_error,
                )
            except Exception:
                logger.debug("emit_schema_violation_meta failed", exc_info=True)

    except Exception:
        logger.exception(
            "emit_agent_event swallowed unexpected error",
            extra={"agent": agent_name, "event_type": event_type},
        )


def _emit_schema_violation_meta(
    *,
    agent_name: str,
    event_type: str,
    novel_id: int,
    chapter_num: int | None,
    trace_id: str | None,
    schema_error: str,
) -> None:
    """对 payload 校验失败的事件再打一条元事件（§4.3.1 写入侧）。

    单独函数避免与主 emit 互相递归出错；这条元事件本身不会再校验。
    """
    db = SessionLocal()
    try:
        meta = AgentEvent(
            trace_id=trace_id,
            novel_id=novel_id,
            chapter_num=chapter_num,
            agent_name="agent_events_meta",
            event_type="schema_violation",
            verdict="warn",
            error_code="payload_schema_violation",
            error_category="policy",
            payload={
                "source_agent": agent_name,
                "source_event_type": event_type,
                "schema_error": schema_error,
            },
        )
        db.add(meta)
        db.commit()
    except Exception:
        logger.exception("emit_schema_violation_meta persist failed")
        try:
            db.rollback()
        except Exception:
            logger.debug("schema_violation meta rollback failed", exc_info=True)
    finally:
        try:
            db.close()
        except Exception:
            logger.debug("schema_violation meta close failed", exc_info=True)
    _bump_counter("agent_events_meta", "schema_violation", "warn")
