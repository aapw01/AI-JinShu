"""Multi-LLM support via LangChain adapters with provider registry and fallback."""
import logging
import time
from typing import Any
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.config import get_settings
from app.core.llm_usage import record_usage_from_response
from app.core.logging_config import log_event
from app.services.system_settings.runtime import (
    get_default_model_for_type,
    get_enabled_provider_order,
    get_provider_default_model,
    get_provider_runtime,
)

logger = logging.getLogger(__name__)


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


def _resolve_api_key(provider: str) -> str:
    runtime_provider = get_provider_runtime(provider)
    if runtime_provider and runtime_provider.get("api_key"):
        return str(runtime_provider.get("api_key") or "")

    settings = get_settings()
    provider_key = (provider or "").strip().lower()
    selected_provider = (settings.default_llm_provider or "openai").strip().lower()
    if settings.llm_api_key:
        if provider_key == selected_provider:
            return settings.llm_api_key
    if provider_key == "openai":
        return settings.openai_api_key or ""
    if provider_key == "anthropic":
        return settings.anthropic_api_key or ""
    if provider_key == "gemini":
        return settings.gemini_api_key or ""
    return settings.openai_api_key or ""


def _resolve_base_url(provider: str) -> str | None:
    runtime_provider = get_provider_runtime(provider)
    if runtime_provider and runtime_provider.get("base_url"):
        return str(runtime_provider.get("base_url") or "").rstrip("/") or None

    settings = get_settings()
    provider_key = (provider or "").strip().lower()
    selected_provider = (settings.default_llm_provider or "openai").strip().lower()
    if settings.llm_base_url:
        if provider_key == selected_provider:
            base = settings.llm_base_url.rstrip("/")
            if provider == "anthropic" and base.endswith("/v1"):
                # Anthropic SDK appends /v1/messages internally.
                base = base[:-3].rstrip("/")
            return base
    if provider == "openai":
        return settings.openai_base_url
    if provider == "anthropic":
        base = settings.anthropic_base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3].rstrip("/")
        return base
    if provider == "gemini":
        return settings.gemini_base_url
    return None


_LLM_REQUEST_TIMEOUT = 120

def _adapter_for_provider(provider_key: str) -> str:
    runtime_provider = get_provider_runtime(provider_key)
    if runtime_provider and runtime_provider.get("adapter_type"):
        return str(runtime_provider.get("adapter_type"))
    if provider_key == "anthropic":
        return "anthropic"
    if provider_key == "gemini":
        return "gemini"
    return "openai_compatible"


def _resolve_chat_provider_and_model(provider: str | None, model: str | None) -> tuple[str, str]:
    settings = get_settings()
    default_chat = get_default_model_for_type("chat") or {}
    provider_key = (
        (provider or "").strip().lower()
        or str(default_chat.get("provider_key") or "").strip().lower()
        or (settings.default_llm_provider or "openai").strip().lower()
    )

    resolved_model = (model or "").strip()
    if not resolved_model:
        by_provider_default = get_provider_default_model(provider_key, "chat")
        if by_provider_default:
            resolved_model = by_provider_default
    if not resolved_model:
        if str(default_chat.get("provider_key") or "").strip().lower() == provider_key:
            resolved_model = str(default_chat.get("model_name") or "").strip()
    if not resolved_model:
        resolved_model = settings.default_llm_model or "gpt-4o-mini"
    return provider_key, resolved_model


def _build_chat_model(provider_key: str, model_name: str) -> BaseChatModel:
    adapter_type = _adapter_for_provider(provider_key)
    if adapter_type == "anthropic":
        base_url = _resolve_base_url(provider_key) or "https://api.anthropic.com/v1"
        if base_url.endswith("/v1"):
            base_url = base_url[:-3].rstrip("/")
        return ChatAnthropic(
            base_url=base_url,
            model=model_name,
            api_key=_resolve_api_key(provider_key),
            timeout=_LLM_REQUEST_TIMEOUT,
            max_retries=0,
        )
    if adapter_type == "gemini":
        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=_resolve_api_key(provider_key),
            timeout=_LLM_REQUEST_TIMEOUT,
        )
    return ChatOpenAI(
        base_url=_resolve_base_url(provider_key) or "https://api.openai.com/v1",
        model=model_name,
        api_key=_resolve_api_key(provider_key),
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
    """Get LLM by provider and optional model using system settings with env fallback."""
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
    """Get LLM, trying provider+model first, then fallback chain."""
    try:
        return get_llm(provider, model)
    except Exception as e:
        log_event(
            logger,
            "llm.call.error",
            level=logging.WARNING,
            provider=provider,
            model=model,
            error_class=type(e).__name__,
            error_category="transient",
        )
    fallback_order = get_enabled_provider_order()
    requested = (provider or "").strip().lower()
    for p in fallback_order:
        if p == requested:
            continue
        try:
            return get_llm(p, None)
        except Exception as e:
            log_event(
                logger,
                "llm.fallback.error",
                level=logging.WARNING,
                provider=p,
                error_class=type(e).__name__,
                error_category="transient",
            )
            continue
    log_event(logger, "llm.fallback.exhausted", level=logging.ERROR, error_code="ALL_PROVIDERS_FAILED", error_category="transient")
    return get_llm("openai", None)


def get_embedding_model() -> OpenAIEmbeddings:
    """Get embeddings model (prefers system settings default for embedding)."""
    settings = get_settings()
    embedding_default = get_default_model_for_type("embedding") or {}
    provider_key = str(embedding_default.get("provider_key") or "openai").strip().lower() or "openai"
    model_name = str(embedding_default.get("model_name") or settings.default_embedding_model or "text-embedding-3-small")
    provider_cfg = get_provider_runtime(provider_key)
    adapter = str(provider_cfg.get("adapter_type") if provider_cfg else _adapter_for_provider(provider_key))
    if adapter != "openai_compatible":
        log_event(
            logger,
            "embed.provider.unsupported_adapter",
            level=logging.WARNING,
            provider=provider_key,
            adapter=adapter,
            fallback="openai",
        )
        provider_key = "openai"
    return OpenAIEmbeddings(
        model=model_name,
        api_key=_resolve_api_key(provider_key),
        base_url=_resolve_base_url(provider_key) or "https://api.openai.com/v1",
    )


def embed_query(text: str) -> list[float] | None:
    """Best-effort query embedding. Returns None when embedding is unavailable."""
    if not text.strip():
        return None
    try:
        started = time.perf_counter()
        model = get_embedding_model()
        embedding = model.embed_query(text)
        embedding_default = get_default_model_for_type("embedding") or {}
        model_name = str(embedding_default.get("model_name") or get_settings().default_embedding_model)
        provider_key = str(embedding_default.get("provider_key") or "openai")
        log_event(
            logger,
            "embed.query.success",
            provider=provider_key,
            model=model_name,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return embedding
    except Exception as exc:
        embedding_default = get_default_model_for_type("embedding") or {}
        model_name = str(embedding_default.get("model_name") or get_settings().default_embedding_model)
        provider_key = str(embedding_default.get("provider_key") or "openai")
        log_event(
            logger,
            "embed.query.fallback",
            level=logging.WARNING,
            provider=provider_key,
            model=model_name,
            error_class=type(exc).__name__,
            error_category="transient",
            reason=str(exc)[:300],
        )
        return None
