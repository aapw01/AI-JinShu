"""Structured-output contract invoker for chapter prose."""
from __future__ import annotations

import hashlib
import logging
from contextvars import ContextVar
from typing import Any

from pydantic import ValidationError

from app.core.llm import get_llm
from app.core.llm_usage import record_usage_from_response
from app.services.generation.contracts import ChapterBodySchema, OutputContractError, validate_chapter_body
from app.services.system_settings.runtime import (
    get_effective_runtime_setting,
    get_enabled_provider_order,
    get_provider_runtime,
)

logger = logging.getLogger(__name__)
_LAST_PROMPT_META: ContextVar[dict[str, Any] | None] = ContextVar("llm_contract_last_prompt_meta", default=None)


def _adapter_for_provider(provider_key: str | None) -> str:
    key = str(provider_key or "").strip().lower()
    runtime = get_provider_runtime(key) if key else None
    adapter = str((runtime or {}).get("adapter_type") or "").strip().lower()
    if adapter in {"openai_compatible", "anthropic", "gemini"}:
        return adapter
    if key == "anthropic":
        return "anthropic"
    if key == "gemini":
        return "gemini"
    return "openai_compatible"


def _method_order(adapter_type: str) -> list[str]:
    if adapter_type == "anthropic":
        return ["function_calling", "json_schema"]
    if adapter_type == "gemini":
        return ["json_schema", "function_calling", "json_mode"]
    return ["json_schema", "function_calling", "json_mode"]


def _extract_parsed_body(parsed: Any) -> str:
    if parsed is None:
        return ""
    if isinstance(parsed, ChapterBodySchema):
        return str(parsed.chapter_body or "")
    if hasattr(parsed, "model_dump"):
        data = parsed.model_dump()
        if isinstance(data, dict):
            return str(data.get("chapter_body") or "")
    if isinstance(parsed, dict):
        return str(parsed.get("chapter_body") or "")
    return ""


def _provider_candidates(
    provider: str | None,
    model: str | None,
    max_provider_fallbacks: int,
) -> list[tuple[str | None, str | None]]:
    requested = str(provider or "").strip().lower() or None
    candidates: list[tuple[str | None, str | None]] = []
    if requested:
        candidates.append((requested, model))
    elif provider is not None:
        candidates.append((provider, model))
    else:
        candidates.append((None, model))

    order = get_enabled_provider_order()
    appended = 0
    for item in order:
        key = str(item or "").strip().lower()
        if not key or key == requested:
            continue
        if appended >= max(0, int(max_provider_fallbacks)):
            break
        candidates.append((key, None))
        appended += 1
    return candidates


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()[:16]


def get_last_prompt_meta() -> dict[str, Any] | None:
    value = _LAST_PROMPT_META.get()
    if not value:
        return None
    return dict(value)


def invoke_chapter_body_structured(
    *,
    prompt: str,
    stage: str,
    provider: str | None = None,
    model: str | None = None,
    chapter_num: int | None = None,
    retries: int | None = None,
    min_chars: int | None = None,
    max_provider_fallbacks: int | None = None,
    prompt_template: str | None = None,
    prompt_version: str = "v2",
) -> str:
    """Invoke LLM with strict chapter-body structured contract."""

    schema_retries = int(
        retries
        if retries is not None
        else (get_effective_runtime_setting("llm_output_max_schema_retries", int, 2) or 2)
    )
    schema_retries = max(0, schema_retries)
    min_body_chars = int(
        min_chars
        if min_chars is not None
        else (get_effective_runtime_setting("llm_output_min_chars", int, 120) or 120)
    )
    provider_fallbacks = int(
        max_provider_fallbacks
        if max_provider_fallbacks is not None
        else (get_effective_runtime_setting("llm_output_max_provider_fallbacks", int, 2) or 2)
    )
    provider_fallbacks = max(0, provider_fallbacks)
    p_template = str(prompt_template or stage)
    p_version = str(prompt_version or "v2")
    p_hash = prompt_hash(prompt)

    failures: list[str] = []
    candidates = _provider_candidates(provider, model, provider_fallbacks)

    for candidate_provider, candidate_model in candidates:
        adapter = _adapter_for_provider(candidate_provider)
        methods = _method_order(adapter)
        for method in methods:
            for attempt in range(schema_retries + 1):
                try:
                    llm = get_llm(candidate_provider, candidate_model)
                    kwargs: dict[str, Any] = {
                        "method": method,
                        "include_raw": True,
                    }
                    if adapter == "openai_compatible":
                        kwargs["strict"] = True
                    try:
                        structured = llm.with_structured_output(ChapterBodySchema, **kwargs)
                    except TypeError:
                        kwargs.pop("strict", None)
                        structured = llm.with_structured_output(ChapterBodySchema, **kwargs)
                    result = structured.invoke(prompt)
                    raw_resp: Any = None
                    parsed: Any = None
                    parsing_error: Any = None
                    if isinstance(result, dict) and any(k in result for k in ("raw", "parsed", "parsing_error")):
                        raw_resp = result.get("raw")
                        parsed = result.get("parsed")
                        parsing_error = result.get("parsing_error")
                    else:
                        parsed = result
                    if raw_resp is not None:
                        record_usage_from_response(
                            raw_resp,
                            stage=f"llm.{candidate_provider or 'default'}.{candidate_model or 'default'}.structured.{stage}",
                        )
                    if parsing_error is not None:
                        raise OutputContractError(
                            code="MODEL_OUTPUT_PARSE_FAILED",
                            stage=stage,
                            chapter_num=chapter_num,
                            provider=candidate_provider,
                            model=candidate_model,
                            detail=str(parsing_error),
                            retryable=True,
                        )
                    chapter_body = _extract_parsed_body(parsed)
                    if not chapter_body and raw_resp is not None:
                        chapter_body = _extract_parsed_body(raw_resp)
                    chapter_body = validate_chapter_body(
                        chapter_body,
                        stage=stage,
                        chapter_num=chapter_num,
                        provider=candidate_provider,
                        model=candidate_model,
                        min_chars=min_body_chars,
                    )
                    logger.info(
                        "llm.output.contract.success stage=%s template=%s version=%s prompt_hash=%s provider=%s model=%s method=%s attempt=%s",
                        stage,
                        p_template,
                        p_version,
                        p_hash,
                        candidate_provider,
                        candidate_model,
                        method,
                        attempt + 1,
                    )
                    _LAST_PROMPT_META.set(
                        {
                            "stage": stage,
                            "prompt_template": p_template,
                            "prompt_version": p_version,
                            "prompt_hash": p_hash,
                            "provider": candidate_provider,
                            "model": candidate_model,
                        }
                    )
                    return chapter_body
                except ValidationError as exc:
                    err = OutputContractError(
                        code="MODEL_OUTPUT_SCHEMA_INVALID",
                        stage=stage,
                        chapter_num=chapter_num,
                        provider=candidate_provider,
                        model=candidate_model,
                        detail=str(exc),
                        retryable=True,
                    )
                    failures.append(str(err))
                except OutputContractError as exc:
                    failures.append(str(exc))
                except Exception as exc:
                    failures.append(
                        f"stage={stage} provider={candidate_provider} model={candidate_model} method={method} call_error={type(exc).__name__}: {exc}"
                    )
                logger.warning(
                    "llm.output.contract.retry stage=%s template=%s version=%s prompt_hash=%s provider=%s model=%s method=%s attempt=%s",
                    stage,
                    p_template,
                    p_version,
                    p_hash,
                    candidate_provider,
                    candidate_model,
                    method,
                    attempt + 1,
                )

                if attempt >= schema_retries:
                    break

    detail = " | ".join(failures[-6:]) if failures else "no usable output"
    logger.error(
        "llm.output.contract.exhausted stage=%s template=%s version=%s prompt_hash=%s provider=%s model=%s detail=%s",
        stage,
        p_template,
        p_version,
        p_hash,
        provider,
        model,
        detail,
    )
    _LAST_PROMPT_META.set(
        {
            "stage": stage,
            "prompt_template": p_template,
            "prompt_version": p_version,
            "prompt_hash": p_hash,
            "provider": provider,
            "model": model,
            "error": detail,
        }
    )
    raise OutputContractError(
        code="MODEL_OUTPUT_CONTRACT_EXHAUSTED",
        stage=stage,
        chapter_num=chapter_num,
        provider=provider,
        model=model,
        detail=detail,
        retryable=True,
    )
