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

logger = logging.getLogger(__name__)


def _resolve_api_key(provider: str) -> str:
    settings = get_settings()
    if settings.llm_api_key:
        return settings.llm_api_key
    if provider == "openai":
        return settings.openai_api_key or ""
    if provider == "anthropic":
        return settings.anthropic_api_key or ""
    if provider == "gemini":
        return settings.gemini_api_key or ""
    return ""


def _resolve_base_url(provider: str) -> str | None:
    settings = get_settings()
    if settings.llm_base_url:
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


_REGISTRY = {
    "openai": lambda model=None: ChatOpenAI(
        base_url=_resolve_base_url("openai") or "https://api.openai.com/v1",
        model=model or get_settings().default_llm_model,
        api_key=_resolve_api_key("openai"),
    ),
    "anthropic": lambda model=None: ChatAnthropic(
        base_url=_resolve_base_url("anthropic") or "https://api.anthropic.com/v1",
        model=model or "claude-3-sonnet-20240229",
        api_key=_resolve_api_key("anthropic"),
    ),
    "gemini": lambda model=None: ChatGoogleGenerativeAI(
        model=model or "gemini-pro",
        google_api_key=_resolve_api_key("gemini"),
    ),
}

_FALLBACK_ORDER = ["openai", "anthropic", "gemini"]


class _TrackedLLMProxy:
    """Thin proxy that records provider usage for every invoke call."""

    def __init__(self, inner: Any, *, stage_prefix: str):
        self._inner = inner
        self._stage_prefix = stage_prefix

    def invoke(self, *args: Any, **kwargs: Any):
        if not hasattr(self._inner, "invoke"):
            return self._inner
        resp = self._inner.invoke(*args, **kwargs)
        record_usage_from_response(resp, stage=self._stage_prefix)
        return resp

    async def ainvoke(self, *args: Any, **kwargs: Any):
        if not hasattr(self._inner, "ainvoke"):
            return self._inner
        resp = await self._inner.ainvoke(*args, **kwargs)
        record_usage_from_response(resp, stage=self._stage_prefix)
        return resp

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
    """Get LLM by provider and optional model. Falls back to openai if provider unknown."""
    settings = get_settings()
    prov = provider or settings.default_llm_provider
    if prov not in _REGISTRY:
        log_event(logger, "llm.provider.unknown", level=logging.WARNING, provider=prov, fallback="openai")
        prov = "openai"
    log_event(logger, "llm.call.start", provider=prov, model=model or settings.default_llm_model)
    started = time.perf_counter()
    llm = _REGISTRY[prov](model)
    log_event(
        logger,
        "llm.call.success",
        provider=prov,
        model=model or settings.default_llm_model,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    stage_prefix = f"llm.{prov}.{model or settings.default_llm_model}"
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
    for p in _FALLBACK_ORDER:
        try:
            return get_llm(p)
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
    return get_llm("openai")


def get_embedding_model() -> OpenAIEmbeddings:
    """Get embeddings model (OpenAI-compatible endpoint)."""
    settings = get_settings()
    return OpenAIEmbeddings(
        model=settings.default_embedding_model,
        api_key=_resolve_api_key("openai"),
        base_url=_resolve_base_url("openai") or "https://api.openai.com/v1",
    )


def embed_query(text: str) -> list[float] | None:
    """Best-effort query embedding. Returns None when embedding is unavailable."""
    if not text.strip():
        return None
    try:
        started = time.perf_counter()
        model = get_embedding_model()
        embedding = model.embed_query(text)
        log_event(
            logger,
            "embed.query.success",
            provider="openai",
            model=get_settings().default_embedding_model,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return embedding
    except Exception as exc:
        log_event(
            logger,
            "embed.query.fallback",
            level=logging.WARNING,
            provider="openai",
            model=get_settings().default_embedding_model,
            error_class=type(exc).__name__,
            error_category="transient",
            reason=str(exc)[:300],
        )
        return None
