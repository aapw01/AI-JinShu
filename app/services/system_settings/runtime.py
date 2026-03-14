"""Runtime resolver for system settings with DB > env precedence."""

from __future__ import annotations

import copy
import logging
import time
from typing import Any, Callable

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.system_settings.crypto import decrypt_api_key, get_security_mode, mask_secret
from app.services.system_settings.repository import (
    RUNTIME_SETTING_KEYS,
    load_model_settings_db,
    list_runtime_overrides,
)

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 5.0
_model_cache: dict[str, Any] = {"ts": 0.0, "value": None}
_runtime_cache: dict[str, Any] = {"ts": 0.0, "value": None}


def invalidate_caches() -> None:
    _model_cache["ts"] = 0.0
    _model_cache["value"] = None
    _runtime_cache["ts"] = 0.0
    _runtime_cache["value"] = None


def _is_cache_fresh(cache_ts: float) -> bool:
    return (time.monotonic() - float(cache_ts or 0.0)) < _CACHE_TTL_SECONDS


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _clean_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _infer_primary_protocol(provider: str, base_url: str | None, protocol_override: str | None) -> str:
    if protocol_override:
        return protocol_override
    if base_url:
        return "openai_compatible"
    if provider == "anthropic":
        return "anthropic"
    if provider == "gemini":
        return "gemini"
    return "openai_compatible"


def _infer_embedding_protocol(base_url: str | None, protocol_override: str | None) -> str:
    if protocol_override:
        return protocol_override
    if base_url:
        return "openai_compatible"
    return "openai_compatible"


def _env_primary_chat() -> dict[str, Any]:
    settings = get_settings()
    provider = str(settings.llm_provider or "openai").strip().lower() or "openai"
    model = str(settings.llm_model or "gpt-4o-mini").strip() or "gpt-4o-mini"
    base_url = _clean_text(settings.llm_base_url)
    api_key = str(settings.llm_api_key or "").strip()
    protocol_override = _clean_text(settings.llm_protocol_override)
    if protocol_override and protocol_override not in ("openai_compatible", "gemini", "anthropic"):
        protocol_override = None
    protocol = _infer_primary_protocol(provider, base_url, protocol_override)
    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "protocol_override": protocol_override,
        "resolved_protocol": protocol,
        "api_key": api_key,
        "api_key_source": "env" if api_key else "none",
        "api_key_is_encrypted": False,
        "source": "env",
    }


def _env_embedding() -> dict[str, Any]:
    settings = get_settings()
    base_url = _clean_text(settings.embedding_base_url)
    api_key = str(settings.embedding_api_key or "").strip()
    protocol = _infer_embedding_protocol(base_url, None)
    return {
        "enabled": bool(settings.embedding_enabled),
        "model": _clean_text(settings.embedding_model),
        "reuse_primary_connection": bool(settings.embedding_reuse_primary_connection),
        "base_url": base_url,
        "protocol_override": None,
        "resolved_protocol": protocol,
        "api_key": api_key,
        "api_key_source": "env" if api_key else "none",
        "api_key_is_encrypted": False,
        "source": "env",
    }


def _hydrate_primary(raw: dict[str, Any], source: str) -> dict[str, Any]:
    provider = str(raw.get("provider") or "openai").strip().lower() or "openai"
    model = str(raw.get("model") or "gpt-4o-mini").strip() or "gpt-4o-mini"
    base_url = _clean_text(raw.get("base_url"))
    protocol_override = _clean_text(raw.get("protocol_override"))
    api_key = decrypt_api_key(raw.get("api_key_ciphertext"), _to_bool(raw.get("api_key_is_encrypted")))
    protocol = _infer_primary_protocol(provider, base_url, protocol_override)
    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "protocol_override": protocol_override,
        "resolved_protocol": protocol,
        "api_key": api_key,
        "api_key_source": source if api_key else "none",
        "api_key_is_encrypted": _to_bool(raw.get("api_key_is_encrypted")),
        "source": source,
    }


def _hydrate_embedding(raw: dict[str, Any], source: str) -> dict[str, Any]:
    base_url = _clean_text(raw.get("base_url"))
    protocol_override = _clean_text(raw.get("protocol_override"))
    api_key = decrypt_api_key(raw.get("api_key_ciphertext"), _to_bool(raw.get("api_key_is_encrypted")))
    protocol = _infer_embedding_protocol(base_url, protocol_override)
    return {
        "enabled": _to_bool(raw.get("enabled", False)),
        "model": _clean_text(raw.get("model")),
        "reuse_primary_connection": _to_bool(raw.get("reuse_primary_connection", True)),
        "base_url": base_url,
        "protocol_override": protocol_override,
        "resolved_protocol": protocol,
        "api_key": api_key,
        "api_key_source": source if api_key else "none",
        "api_key_is_encrypted": _to_bool(raw.get("api_key_is_encrypted")),
        "source": source,
    }


def _load_model_cache_value() -> dict[str, Any]:
    db = SessionLocal()
    try:
        db_values = load_model_settings_db(db)
    finally:
        db.close()

    if db_values.get("primary_chat"):
        primary = _hydrate_primary(db_values["primary_chat"], "db")
    else:
        primary = _env_primary_chat()

    if db_values.get("embedding"):
        embedding = _hydrate_embedding(db_values["embedding"], "db")
    else:
        embedding = _env_embedding()

    return {
        "primary_chat": primary,
        "embedding": embedding,
        "security_mode": get_security_mode(),
    }


def get_effective_model_config() -> dict[str, Any]:
    if _is_cache_fresh(_model_cache["ts"]) and _model_cache.get("value") is not None:
        return copy.deepcopy(_model_cache["value"])
    value = _load_model_cache_value()
    _model_cache["value"] = value
    _model_cache["ts"] = time.monotonic()
    return copy.deepcopy(value)


def get_primary_chat_runtime() -> dict[str, Any]:
    return get_effective_model_config().get("primary_chat", {})


def get_embedding_runtime() -> dict[str, Any]:
    return get_effective_model_config().get("embedding", {})


def get_default_model_for_type(model_type: str) -> dict[str, Any] | None:
    model_type = (model_type or "").strip().lower()
    config = get_effective_model_config()
    if model_type == "chat":
        primary = config.get("primary_chat") or {}
        return {
            "provider_key": primary.get("provider"),
            "model_name": primary.get("model"),
            "source": primary.get("source", "env"),
        }
    if model_type == "embedding":
        embedding = config.get("embedding") or {}
        if not embedding.get("enabled"):
            return None
        return {
            "provider_key": "primary" if embedding.get("reuse_primary_connection") else "openai",
            "model_name": embedding.get("model"),
            "source": embedding.get("source", "env"),
        }
    return None


def _load_runtime_cache_value() -> dict[str, Any]:
    db = SessionLocal()
    try:
        overrides = list_runtime_overrides(db)
    finally:
        db.close()
    return {k: overrides[k] for k in overrides.keys() if k in RUNTIME_SETTING_KEYS}


def _cast_value(value: Any, cast: Callable[[Any], Any] | type | None) -> Any:
    if cast is None:
        return value
    if cast is bool:
        return _to_bool(value)
    return cast(value)


def get_effective_runtime_setting(
    key: str,
    cast: Callable[[Any], Any] | type | None = None,
    default: Any = None,
) -> Any:
    if not _is_cache_fresh(_runtime_cache["ts"]) or _runtime_cache.get("value") is None:
        _runtime_cache["value"] = _load_runtime_cache_value()
        _runtime_cache["ts"] = time.monotonic()

    overrides: dict[str, Any] = _runtime_cache["value"] or {}
    if key in overrides:
        value = overrides[key]
    else:
        value = getattr(get_settings(), key, default)
    if value is None:
        value = default
    try:
        return _cast_value(value, cast)
    except Exception:
        logger.warning("runtime setting cast failed key=%s value=%r", key, value, exc_info=True)
        fallback = getattr(get_settings(), key, default)
        return _cast_value(fallback, cast)


def get_runtime_settings_with_sources(keys: list[str]) -> dict[str, dict[str, Any]]:
    if not _is_cache_fresh(_runtime_cache["ts"]) or _runtime_cache.get("value") is None:
        _runtime_cache["value"] = _load_runtime_cache_value()
        _runtime_cache["ts"] = time.monotonic()

    overrides: dict[str, Any] = _runtime_cache["value"] or {}
    settings = get_settings()
    payload: dict[str, dict[str, Any]] = {}
    for key in keys:
        if key in overrides:
            payload[key] = {"value": overrides[key], "source": "db"}
        else:
            payload[key] = {"value": getattr(settings, key, None), "source": "env"}
    return payload


def get_runtime_overrides() -> dict[str, Any]:
    if not _is_cache_fresh(_runtime_cache["ts"]) or _runtime_cache.get("value") is None:
        _runtime_cache["value"] = _load_runtime_cache_value()
        _runtime_cache["ts"] = time.monotonic()
    return copy.deepcopy(_runtime_cache["value"] or {})


def get_model_settings_for_admin(*, include_secrets: bool = False) -> dict[str, Any]:
    config = get_effective_model_config()
    primary = config.get("primary_chat") or {}
    embedding = config.get("embedding") or {}
    return {
        "primary_chat": {
            "provider": primary.get("provider"),
            "model": primary.get("model"),
            "base_url": primary.get("base_url"),
            "protocol_override": primary.get("protocol_override"),
            "resolved_protocol": primary.get("resolved_protocol"),
            "api_key_value": primary.get("api_key") if include_secrets else None,
            "api_key_masked": mask_secret(primary.get("api_key")),
            "api_key_source": primary.get("api_key_source", "none"),
            "api_key_is_encrypted": bool(primary.get("api_key_is_encrypted", False)),
            "source": primary.get("source", "env"),
        },
        "embedding": {
            "enabled": bool(embedding.get("enabled", False)),
            "model": embedding.get("model"),
            "reuse_primary_connection": bool(embedding.get("reuse_primary_connection", True)),
            "base_url": embedding.get("base_url"),
            "protocol_override": embedding.get("protocol_override"),
            "resolved_protocol": embedding.get("resolved_protocol"),
            "api_key_value": embedding.get("api_key") if include_secrets else None,
            "api_key_masked": mask_secret(embedding.get("api_key")),
            "api_key_source": embedding.get("api_key_source", "none"),
            "api_key_is_encrypted": bool(embedding.get("api_key_is_encrypted", False)),
            "source": embedding.get("source", "env"),
        },
        "security_mode": config.get("security_mode", "plaintext"),
    }
