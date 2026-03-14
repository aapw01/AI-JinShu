"""Multi-LLM support via a single canonical primary model connection."""

from __future__ import annotations

import logging
import time
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.core.llm_usage import record_usage_from_response
from app.core.logging_config import log_event
from app.services.system_settings.runtime import get_embedding_runtime, get_primary_chat_runtime

logger = logging.getLogger(__name__)

_LLM_REQUEST_TIMEOUT = 120
_VALID_ADAPTER_TYPES = frozenset({"openai_compatible", "gemini", "anthropic"})


def _coerce_part_text(part: Any) -> str:
    if part is None:
        return ""
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        text = part.get("text")
        if isinstance(text, str):
            return text
        content = part.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(x for x in (_coerce_part_text(i) for i in content) if x)
        return ""
    text_attr = getattr(part, "text", None)
    if isinstance(text_attr, str):
        return text_attr
    content_attr = getattr(part, "content", None)
    if isinstance(content_attr, str):
        return content_attr
    if isinstance(content_attr, list):
        return "\n".join(x for x in (_coerce_part_text(i) for i in content_attr) if x)
    return str(part)


def response_to_text(resp: Any) -> str:
    """Normalize LangChain/OpenAI response content to plain text."""
    content = getattr(resp, "content", resp)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p for p in (_coerce_part_text(item) for item in content) if p]
        return "\n".join(parts)
    return str(content or "")


def _resolve_api_key(_provider: str | None = None) -> str:
    return str(get_primary_chat_runtime().get("api_key") or "")


def _resolve_base_url(_provider: str | None = None) -> str | None:
    base_url = str(get_primary_chat_runtime().get("base_url") or "").strip()
    return base_url.rstrip("/") or None


def resolve_effective_adapter(provider_key: str | None) -> tuple[str, str]:
    primary = get_primary_chat_runtime()
    provider = str(provider_key or primary.get("provider") or "openai").strip().lower() or "openai"
    protocol_override = str(primary.get("protocol_override") or "").strip().lower() or None
    base_url = str(primary.get("base_url") or "").strip() or None
    if protocol_override in _VALID_ADAPTER_TYPES:
        return protocol_override, "override"
    if base_url:
        return "openai_compatible", "auto_infer"
    if provider == "anthropic":
        return "anthropic", "auto_native"
    if provider == "gemini":
        return "gemini", "auto_native"
    return "openai_compatible", "auto_native"


def _resolve_chat_provider_and_model(provider: str | None, model: str | None) -> tuple[str, str]:
    primary = get_primary_chat_runtime()
    resolved_provider = str(primary.get("provider") or "openai").strip().lower() or "openai"
    resolved_model = str(model or primary.get("model") or "gpt-4o-mini").strip() or "gpt-4o-mini"
    requested_provider = str(provider or "").strip().lower()
    if requested_provider and requested_provider != resolved_provider:
        log_event(
            logger,
            "llm.provider_override.ignored",
            level=logging.DEBUG,
            requested_provider=requested_provider,
            effective_provider=resolved_provider,
        )
    return resolved_provider, resolved_model


def _build_chat_model(provider_key: str, model_name: str) -> BaseChatModel:
    adapter_type, adapter_source = resolve_effective_adapter(provider_key)
    primary = get_primary_chat_runtime()
    resolved_base_url = _resolve_base_url(provider_key)
    resolved_api_key = _resolve_api_key(provider_key)

    log_event(
        logger,
        "llm.adapter.resolved",
        provider=provider_key,
        effective_adapter=adapter_type,
        adapter_source=adapter_source,
        configured_provider=primary.get("provider"),
        base_url=(resolved_base_url or "")[:60],
    )

    if adapter_type == "anthropic":
        base_url = resolved_base_url or "https://api.anthropic.com"
        if base_url.endswith("/v1"):
            base_url = base_url[:-3].rstrip("/")
        return ChatAnthropic(
            base_url=base_url,
            model=model_name,
            api_key=resolved_api_key,
            timeout=_LLM_REQUEST_TIMEOUT,
            max_retries=0,
        )
    if adapter_type == "gemini":
        kwargs: dict[str, Any] = {
            "model": model_name,
            "google_api_key": resolved_api_key,
            "timeout": _LLM_REQUEST_TIMEOUT,
        }
        if resolved_base_url:
            kwargs["base_url"] = resolved_base_url
        return ChatGoogleGenerativeAI(**kwargs)
    return ChatOpenAI(
        base_url=resolved_base_url or "https://api.openai.com/v1",
        model=model_name,
        api_key=resolved_api_key,
        request_timeout=_LLM_REQUEST_TIMEOUT,
        max_retries=0,
    )


_RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    OSError,
)

_MAX_RETRIES = 3
_RETRY_BACKOFF = [1.0, 2.0, 4.0]


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, _RETRYABLE_EXCEPTIONS):
        return True
    name = type(exc).__name__
    if any(kw in name for kw in ("Timeout", "Connection", "RateLimit", "ServiceUnavailable", "APIConnectionError")):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int) and status in {429, 500, 502, 503, 504}:
        return True
    return False


class _TrackedLLMProxy:
    """Thin proxy that records provider usage for every invoke call with retry."""

    def __init__(self, inner: Any, *, stage_prefix: str):
        self._inner = inner
        self._stage_prefix = stage_prefix

    def invoke(self, *args: Any, **kwargs: Any):
        if not hasattr(self._inner, "invoke"):
            return self._inner
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._inner.invoke(*args, **kwargs)
                record_usage_from_response(resp, stage=self._stage_prefix)
                return resp
            except Exception as exc:
                last_exc = exc
                if not _is_retryable(exc) or attempt >= _MAX_RETRIES - 1:
                    raise
                delay = _RETRY_BACKOFF[attempt] if attempt < len(_RETRY_BACKOFF) else 4.0
                log_event(
                    logger,
                    "llm.invoke.retry",
                    level=logging.WARNING,
                    attempt=attempt + 1,
                    error_class=type(exc).__name__,
                    delay=delay,
                )
                time.sleep(delay)
        raise last_exc  # type: ignore[misc]

    async def ainvoke(self, *args: Any, **kwargs: Any):
        import asyncio

        if not hasattr(self._inner, "ainvoke"):
            return self._inner
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self._inner.ainvoke(*args, **kwargs)
                record_usage_from_response(resp, stage=self._stage_prefix)
                return resp
            except Exception as exc:
                last_exc = exc
                if not _is_retryable(exc) or attempt >= _MAX_RETRIES - 1:
                    raise
                delay = _RETRY_BACKOFF[attempt] if attempt < len(_RETRY_BACKOFF) else 4.0
                log_event(
                    logger,
                    "llm.ainvoke.retry",
                    level=logging.WARNING,
                    attempt=attempt + 1,
                    error_class=type(exc).__name__,
                    delay=delay,
                )
                await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    def __getattr__(self, item: str):
        return getattr(self._inner, item)

    def __getitem__(self, item: Any):
        return self._inner[item]

    def __iter__(self):
        return iter(self._inner)

    def __len__(self) -> int:
        return len(self._inner)

    def __contains__(self, item: Any) -> bool:
        return item in self._inner

    def __repr__(self) -> str:
        return repr(self._inner)


def get_llm(provider: str | None = None, model: str | None = None) -> BaseChatModel:
    """Get LLM from the canonical primary model connection."""
    prov, resolved_model = _resolve_chat_provider_and_model(provider, model)
    log_event(logger, "llm.call.start", provider=prov, model=resolved_model)
    started = time.perf_counter()
    llm = _build_chat_model(prov, resolved_model)
    log_event(
        logger,
        "llm.call.success",
        provider=prov,
        model=resolved_model,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    stage_prefix = f"llm.{prov}.{resolved_model}"
    return _TrackedLLMProxy(llm, stage_prefix=stage_prefix)  # type: ignore[return-value]


def get_llm_with_fallback(provider: str | None, model: str | None) -> BaseChatModel:
    """Compatibility wrapper. The system no longer supports multi-provider fallback."""
    return get_llm(provider, model)


def get_embedding_model() -> OpenAIEmbeddings:
    """Build the single configured embedding connection."""
    embedding = get_embedding_runtime()
    if not embedding.get("enabled"):
        raise RuntimeError("Embedding is disabled")

    model_name = str(embedding.get("model") or "").strip()
    if not model_name:
        raise RuntimeError("Embedding model is not configured")

    if embedding.get("reuse_primary_connection"):
        primary = get_primary_chat_runtime()
        if str(primary.get("resolved_protocol") or "") != "openai_compatible":
            raise RuntimeError("Embedding cannot reuse a non-OpenAI-compatible primary connection")
        return OpenAIEmbeddings(
            model=model_name,
            api_key=str(primary.get("api_key") or ""),
            base_url=str(primary.get("base_url") or "").strip() or "https://api.openai.com/v1",
        )

    if str(embedding.get("resolved_protocol") or "openai_compatible") != "openai_compatible":
        raise RuntimeError("Dedicated embedding connection currently only supports OpenAI-compatible protocol")
    return OpenAIEmbeddings(
        model=model_name,
        api_key=str(embedding.get("api_key") or ""),
        base_url=str(embedding.get("base_url") or "").strip() or "https://api.openai.com/v1",
    )


def embed_query(text: str) -> list[float] | None:
    """Best-effort query embedding. Returns None when embedding is unavailable."""
    if not text.strip():
        return None
    try:
        started = time.perf_counter()
        model = get_embedding_model()
        embedding = model.embed_query(text)
        runtime = get_embedding_runtime()
        log_event(
            logger,
            "embed.query.success",
            provider="primary" if runtime.get("reuse_primary_connection") else "openai_compatible",
            model=str(runtime.get("model") or ""),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return embedding
    except Exception as exc:
        runtime = get_embedding_runtime()
        log_event(
            logger,
            "embed.query.fallback",
            level=logging.WARNING,
            provider="primary" if runtime.get("reuse_primary_connection") else "openai_compatible",
            model=str(runtime.get("model") or ""),
            error_class=type(exc).__name__,
            error_category="permanent",
            reason=str(exc)[:300],
        )
        return None
