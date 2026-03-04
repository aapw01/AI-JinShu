"""Runtime resolver for system settings with DB > env precedence."""

from __future__ import annotations

import copy
import logging
import time
from typing import Any, Callable

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.services.system_settings.crypto import decrypt_api_key, get_security_mode, mask_secret
from app.services.system_settings.repository import MODEL_TYPES, RUNTIME_SETTING_KEYS, list_model_settings_db, list_runtime_overrides

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


def _resolve_env_api_key(provider: str) -> str:
    s = get_settings()
    selected_provider = (s.default_llm_provider or "openai").strip().lower()
    if s.llm_api_key:
        if provider == selected_provider:
            return s.llm_api_key
    if provider == "openai":
        return s.openai_api_key or ""
    if provider == "anthropic":
        return s.anthropic_api_key or ""
    if provider == "gemini":
        return s.gemini_api_key or ""
    return ""


def _resolve_env_base_url(provider: str) -> str | None:
    s = get_settings()
    selected_provider = (s.default_llm_provider or "openai").strip().lower()
    if s.llm_base_url:
        if provider == selected_provider:
            base = s.llm_base_url.rstrip("/")
            if provider == "anthropic" and base.endswith("/v1"):
                base = base[:-3].rstrip("/")
            return base
    if provider == "openai":
        return s.openai_base_url
    if provider == "anthropic":
        base = s.anthropic_base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3].rstrip("/")
        return base
    if provider == "gemini":
        return s.gemini_base_url
    return None


def _adapter_for_provider(provider: str) -> str:
    if provider == "anthropic":
        return "anthropic"
    if provider == "gemini":
        return "gemini"
    return "openai_compatible"


def _env_provider_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    settings = get_settings()
    selected_provider = (settings.default_llm_provider or "openai").strip().lower()
    for idx, provider_key in enumerate(("openai", "anthropic", "gemini"), start=1):
        api_key = _resolve_env_api_key(provider_key)
        base_url = _resolve_env_base_url(provider_key)
        # Show env providers only when explicitly selected or actually configured with a key.
        # This avoids "phantom" providers in admin UI when only one unified env provider is used.
        if not api_key and provider_key != selected_provider:
            continue
        rows.append(
            {
                "provider_key": provider_key,
                "display_name": provider_key,
                "adapter_type": _adapter_for_provider(provider_key),
                "base_url": base_url,
                "api_key": api_key,
                "api_key_source": "env" if api_key else "none",
                "is_enabled": True,
                "priority": 1000 + idx,
                "models": [],
                "source": "env",
            }
        )
    return rows


def _load_model_cache_value() -> dict[str, Any]:
    settings = get_settings()
    db = SessionLocal()
    try:
        db_rows = list_model_settings_db(db)
    finally:
        db.close()

    env_rows = _env_provider_rows()
    env_by_key = {row["provider_key"]: row for row in env_rows}

    providers: list[dict[str, Any]] = []
    for row in db_rows:
        provider_key = str(row.get("provider_key") or "").strip().lower()
        env_row = env_by_key.get(provider_key)
        db_api_key = decrypt_api_key(row.get("api_key_ciphertext"), bool(row.get("api_key_is_encrypted")))
        merged = {
            "provider_key": provider_key,
            "display_name": row.get("display_name") or provider_key,
            "adapter_type": row.get("adapter_type") or (env_row.get("adapter_type") if env_row else "openai_compatible"),
            "base_url": (row.get("base_url") or (env_row.get("base_url") if env_row else None)),
            "api_key": db_api_key or (env_row.get("api_key") if env_row else ""),
            "api_key_source": "db" if db_api_key else (("env" if (env_row and env_row.get("api_key")) else "none")),
            "api_key_is_encrypted": bool(row.get("api_key_is_encrypted")),
            "is_enabled": _to_bool(row.get("is_enabled", True)),
            "priority": int(row.get("priority") or 100),
            "models": [],
            "source": "db",
        }
        for m in (row.get("models") if isinstance(row.get("models"), list) else []):
            merged["models"].append(
                {
                    "model_name": str(m.get("model_name") or "").strip(),
                    "model_type": str(m.get("model_type") or "chat").strip().lower(),
                    "is_default": _to_bool(m.get("is_default")),
                    "is_enabled": _to_bool(m.get("is_enabled", True)),
                    "metadata": m.get("metadata") if isinstance(m.get("metadata"), dict) else {},
                    "source": "db",
                }
            )
        providers.append(merged)

    existing_keys = {p["provider_key"] for p in providers}
    for env_row in env_rows:
        if env_row["provider_key"] not in existing_keys:
            providers.append(env_row)

    providers.sort(key=lambda x: (int(x.get("priority") or 1000), str(x.get("provider_key") or "")))

    # Build model defaults from db first.
    default_models: dict[str, dict[str, Any]] = {}
    for model_type in MODEL_TYPES:
        for provider in providers:
            if not _to_bool(provider.get("is_enabled", True)):
                continue
            for model in provider.get("models", []):
                if not _to_bool(model.get("is_enabled", True)):
                    continue
                if str(model.get("model_type")) != model_type:
                    continue
                if _to_bool(model.get("is_default")):
                    default_models[model_type] = {
                        "provider_key": provider["provider_key"],
                        "model_name": model["model_name"],
                        "source": "db",
                    }
                    break
            if model_type in default_models:
                break

    # Env fallback defaults.
    fallback_chat_provider = settings.default_llm_provider or "openai"
    fallback_chat_model = settings.default_llm_model or "gpt-4o-mini"
    if "chat" not in default_models:
        default_models["chat"] = {
            "provider_key": fallback_chat_provider,
            "model_name": fallback_chat_model,
            "source": "env",
        }

    if "embedding" not in default_models:
        fallback_embedding_provider = "openai"
        default_models["embedding"] = {
            "provider_key": fallback_embedding_provider,
            "model_name": settings.default_embedding_model or "text-embedding-3-small",
            "source": "env",
        }

    # Keep image/video nullable when no DB setting exists.
    default_models.setdefault("image", {"provider_key": None, "model_name": None, "source": "none"})
    default_models.setdefault("video", {"provider_key": None, "model_name": None, "source": "none"})

    enabled_order = [p["provider_key"] for p in providers if _to_bool(p.get("is_enabled", True))]
    if not enabled_order:
        enabled_order = [fallback_chat_provider, "openai", "anthropic", "gemini"]

    # Unique, keep order.
    seen: set[str] = set()
    fallback_order: list[str] = []
    for item in enabled_order + ["openai", "anthropic", "gemini"]:
        key = str(item or "").strip().lower()
        if not key or key in seen:
            continue
        fallback_order.append(key)
        seen.add(key)

    return {
        "providers": providers,
        "default_models": default_models,
        "fallback_order": fallback_order,
        "security_mode": get_security_mode(),
    }


def get_effective_model_config() -> dict[str, Any]:
    if _is_cache_fresh(_model_cache["ts"]) and _model_cache.get("value") is not None:
        return copy.deepcopy(_model_cache["value"])
    value = _load_model_cache_value()
    _model_cache["value"] = value
    _model_cache["ts"] = time.monotonic()
    return copy.deepcopy(value)


def get_provider_runtime(provider_key: str) -> dict[str, Any] | None:
    key = (provider_key or "").strip().lower()
    if not key:
        return None
    config = get_effective_model_config()
    for provider in config.get("providers", []):
        if provider.get("provider_key") == key:
            return provider
    return None


def get_default_model_for_type(model_type: str) -> dict[str, Any] | None:
    model_type = (model_type or "").strip().lower()
    config = get_effective_model_config()
    return config.get("default_models", {}).get(model_type)


def get_provider_default_model(provider_key: str, model_type: str = "chat") -> str | None:
    provider = get_provider_runtime(provider_key)
    if not provider:
        return None
    mtype = (model_type or "chat").strip().lower()
    for model in provider.get("models", []):
        if str(model.get("model_type") or "").strip().lower() != mtype:
            continue
        if _to_bool(model.get("is_default")) and _to_bool(model.get("is_enabled", True)):
            return str(model.get("model_name") or "").strip() or None
    return None


def get_enabled_provider_order() -> list[str]:
    config = get_effective_model_config()
    return list(config.get("fallback_order") or [])


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
    providers = []
    for provider in config.get("providers", []):
        providers.append(
            {
                "provider_key": provider.get("provider_key"),
                "display_name": provider.get("display_name"),
                "adapter_type": provider.get("adapter_type"),
                "base_url": provider.get("base_url"),
                "api_key_value": (provider.get("api_key") if include_secrets else None),
                "api_key_masked": mask_secret(provider.get("api_key")),
                "api_key_source": provider.get("api_key_source"),
                "api_key_is_encrypted": bool(provider.get("api_key_is_encrypted")),
                "is_enabled": bool(provider.get("is_enabled", True)),
                "priority": int(provider.get("priority") or 100),
                "models": provider.get("models", []),
                "source": provider.get("source", "env"),
            }
        )
    return {
        "providers": providers,
        "default_models": config.get("default_models", {}),
        "fallback_order": config.get("fallback_order", []),
        "security_mode": config.get("security_mode", "plaintext"),
    }
