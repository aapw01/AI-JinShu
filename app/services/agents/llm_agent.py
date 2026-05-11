"""Shared LLM agent runner (Wave 2).

把 ``render_prompt → llm.with_structured_output → 三种 method 兜底 → emit
agent_event`` 抽象成一个工具函数，让各 agent 文件只关注自身 prompt 与
schema。

设计要点：
- 调用失败、parse 失败一律返回 ``None``，让调用方决定回退（写降级事件 / 跳过 / 重试）。
- 全程不抛异常出去（emit warn 事件）。
- 兼容现有 ``app/services/storyboard/character_prompts.py`` 的 method 兜底
  顺序（``json_schema`` → ``function_calling`` → ``json_mode``）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Type, TypeVar

from pydantic import BaseModel

from app.core.llm import get_llm
from app.core.llm_circuit_breaker import (
    EndpointName,
    record_call,
    select_endpoint,
)
from app.prompts import render_prompt
from app.services.agents.events import emit_agent_event

logger = logging.getLogger(__name__)


T = TypeVar("T", bound=BaseModel)


def run_llm_agent(
    *,
    agent_name: str,
    event_type: str,
    template: str,
    template_kwargs: dict[str, Any],
    schema: Type[T],
    novel_id: int,
    chapter_num: int | None = None,
    novel_version_id: int | None = None,
    extra_event_payload: dict[str, Any] | None = None,
    stage: str | None = None,
) -> T | None:
    """Render → call LLM with structured output → validate → emit event.

    返回 schema 实例或 None。失败统一 emit 一条 ``verdict=fail`` 事件。

    ``stage`` 用于 circuit-breaker / fallback chain 决策。``None`` 时退化为
    ``agent_name`` 自身（即每个 agent 自成一条 stage 维度）。
    """
    stage_key = (stage or agent_name).strip() or "default"
    started = time.perf_counter()
    try:
        prompt = render_prompt(template, **template_kwargs)
    except Exception as exc:
        logger.exception("llm_agent: render_prompt failed agent=%s tpl=%s", agent_name, template)
        emit_agent_event(
            agent_name=agent_name,
            event_type=event_type,
            novel_id=int(novel_id),
            chapter_num=chapter_num,
            novel_version_id=novel_version_id,
            verdict="fail",
            error_code="PROMPT_RENDER_FAILED",
            error_category="permanent",
            payload={"error": str(exc), **(extra_event_payload or {})},
        )
        return None

    last_err: Exception | None = None
    last_method: str | None = None
    last_endpoint: EndpointName | None = None
    tried_endpoints: list[EndpointName] = []
    for _ in range(3):
        endpoint, breaker_state = select_endpoint(
            stage_key, exclude=tuple(tried_endpoints)
        )
        if endpoint in tried_endpoints:
            break
        tried_endpoints.append(endpoint)
        last_endpoint = endpoint
        try:
            llm = get_llm()
        except Exception as exc:
            logger.exception(
                "llm_agent: get_llm failed agent=%s stage=%s endpoint=%s",
                agent_name,
                stage_key,
                endpoint,
            )
            record_call(stage_key, endpoint, success=False)
            last_err = exc
            continue

        for method in ("json_schema", "function_calling", "json_mode"):
            try:
                structured = llm.with_structured_output(
                    schema, method=method, include_raw=True
                )
                payload = structured.invoke(prompt)
                if isinstance(payload, dict) and any(
                    k in payload for k in ("parsed", "raw", "parsing_error")
                ):
                    if payload.get("parsing_error"):
                        raise RuntimeError(str(payload.get("parsing_error")))
                    parsed = payload.get("parsed")
                else:
                    parsed = payload
                if isinstance(parsed, BaseModel):
                    parsed = parsed.model_dump()
                if not isinstance(parsed, dict):
                    raise RuntimeError("invalid structured payload (not dict)")
                validated = schema.model_validate(parsed)
                duration_ms = int((time.perf_counter() - started) * 1000)
                record_call(stage_key, endpoint, success=True)
                emit_agent_event(
                    agent_name=agent_name,
                    event_type=event_type,
                    novel_id=int(novel_id),
                    chapter_num=chapter_num,
                    novel_version_id=novel_version_id,
                    verdict="pass",
                    duration_ms=duration_ms,
                    payload={
                        "method": method,
                        "endpoint": endpoint,
                        "breaker_state": breaker_state,
                        **(extra_event_payload or {}),
                    },
                )
                return validated
            except Exception as exc:
                last_err = exc
                last_method = method
                continue
        # 该 endpoint 三种 method 全失败 → endpoint-level failure
        record_call(stage_key, endpoint, success=False)
        logger.warning(
            "llm_agent endpoint failed stage=%s endpoint=%s method=%s err=%s",
            stage_key,
            endpoint,
            last_method,
            last_err,
        )
        try:
            from app.core.metrics import llm_endpoint_failure_total

            llm_endpoint_failure_total.inc(stage=stage_key, endpoint=str(endpoint))
        except Exception:
            logger.debug("llm_endpoint_failure_total metric failed", exc_info=True)

    duration_ms = int((time.perf_counter() - started) * 1000)
    emit_agent_event(
        agent_name=agent_name,
        event_type=event_type,
        novel_id=int(novel_id),
        chapter_num=chapter_num,
        novel_version_id=novel_version_id,
        verdict="fail",
        duration_ms=duration_ms,
        error_code="STRUCTURED_OUTPUT_FAILED",
        error_category="transient",
        payload={
            "error": str(last_err) if last_err else "unknown",
            "endpoint_attempts": tried_endpoints,
            "last_endpoint": last_endpoint,
            **(extra_event_payload or {}),
        },
    )
    return None


__all__ = ["run_llm_agent"]
