"""Multi-LLM support via LangChain adapters with provider registry and fallback."""
import logging
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_openai import OpenAIEmbeddings
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.config import get_settings

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


def get_llm(provider: str | None = None, model: str | None = None) -> BaseChatModel:
    """Get LLM by provider and optional model. Falls back to openai if provider unknown."""
    settings = get_settings()
    prov = provider or settings.default_llm_provider
    if prov not in _REGISTRY:
        logger.warning(f"Unknown provider '{prov}', falling back to openai")
        prov = "openai"
    return _REGISTRY[prov](model)


def get_llm_with_fallback(provider: str | None, model: str | None) -> BaseChatModel:
    """Get LLM, trying provider+model first, then fallback chain."""
    try:
        return get_llm(provider, model)
    except Exception as e:
        logger.warning(f"Failed to get LLM {provider}/{model}: {e}")
    for p in _FALLBACK_ORDER:
        try:
            return get_llm(p)
        except Exception as e:
            logger.warning(f"Fallback provider {p} failed: {e}")
            continue
    logger.error("All LLM providers failed, using openai as last resort")
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
        model = get_embedding_model()
        return model.embed_query(text)
    except Exception as exc:
        logger.warning("Embedding query failed, fallback to lexical search: %s", exc)
        return None
