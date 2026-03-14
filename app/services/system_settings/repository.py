"""Persistence layer for admin-managed system settings."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.novel import SystemRuntimeSetting
from app.services.system_settings.crypto import encrypt_api_key

MODEL_PROVIDERS = ("openai", "anthropic", "gemini")
PROTOCOL_OVERRIDES = ("openai_compatible", "anthropic", "gemini")
MODEL_SETTINGS_PRIMARY_KEY = "model_primary_chat"
MODEL_SETTINGS_EMBEDDING_KEY = "model_embedding"

RUNTIME_SETTING_KEYS = frozenset(
    {
        "creation_scheduler_enabled",
        "creation_default_max_concurrent_tasks",
    }
)

RUNTIME_SETTING_RULES: dict[str, dict[str, Any]] = {
    "creation_scheduler_enabled": {"kind": "bool"},
    "creation_default_max_concurrent_tasks": {"kind": "int", "min": 1, "max": 1000},
}


class SettingsValidationError(ValueError):
    """Raised when settings payload is invalid."""


def _normalize_key(value: str) -> str:
    return (value or "").strip().lower()


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _clean_optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _validate_protocol_override(value: Any, *, allow_none: bool = True) -> str | None:
    if value in (None, ""):
        return None if allow_none else "openai_compatible"
    normalized = _normalize_key(str(value))
    if normalized not in PROTOCOL_OVERRIDES:
        raise SettingsValidationError(f"unsupported protocol_override: {value}")
    return normalized


def _coerce_runtime_value(key: str, value: Any) -> Any:
    rule = RUNTIME_SETTING_RULES.get(key)
    if not rule:
        raise SettingsValidationError(f"unsupported runtime setting key: {key}")

    kind = str(rule.get("kind") or "")
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        raise SettingsValidationError(f"invalid boolean value for {key}: {value!r}")

    if kind != "int":
        raise SettingsValidationError(f"unsupported runtime setting type for key: {key}")

    if isinstance(value, bool):
        raise SettingsValidationError(f"invalid value for {key}: expected integer, got bool")
    try:
        normalized = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise SettingsValidationError(f"invalid integer value for {key}: {value!r}") from exc

    min_value = rule.get("min")
    max_value = rule.get("max")
    if isinstance(min_value, int) and normalized < min_value:
        raise SettingsValidationError(f"{key} must be >= {min_value}")
    if isinstance(max_value, int) and normalized > max_value:
        raise SettingsValidationError(f"{key} must be <= {max_value}")
    return normalized


def _load_setting_rows(db: Session, keys: list[str]) -> dict[str, SystemRuntimeSetting]:
    rows = (
        db.execute(select(SystemRuntimeSetting).where(SystemRuntimeSetting.setting_key.in_(keys)))
        .scalars()
        .all()
    )
    return {row.setting_key: row for row in rows}


def load_model_settings_db(db: Session) -> dict[str, dict[str, Any]]:
    rows = _load_setting_rows(db, [MODEL_SETTINGS_PRIMARY_KEY, MODEL_SETTINGS_EMBEDDING_KEY])
    primary = rows.get(MODEL_SETTINGS_PRIMARY_KEY)
    embedding = rows.get(MODEL_SETTINGS_EMBEDDING_KEY)
    return {
        "primary_chat": primary.setting_value_json if primary and isinstance(primary.setting_value_json, dict) else {},
        "embedding": embedding.setting_value_json if embedding and isinstance(embedding.setting_value_json, dict) else {},
    }


def _normalize_primary_payload(payload: dict[str, Any]) -> dict[str, Any]:
    provider = _normalize_key(str(payload.get("provider") or ""))
    if provider not in MODEL_PROVIDERS:
        raise SettingsValidationError("primary_chat.provider must be one of: openai, anthropic, gemini")

    model = str(payload.get("model") or "").strip()
    if not model:
        raise SettingsValidationError("primary_chat.model is required")

    return {
        "provider": provider,
        "model": model,
        "base_url": _clean_optional_text(payload.get("base_url")),
        "protocol_override": _validate_protocol_override(payload.get("protocol_override")),
    }


def _normalize_embedding_payload(payload: dict[str, Any]) -> dict[str, Any]:
    enabled = _to_bool(payload.get("enabled", False))
    reuse_primary_connection = _to_bool(payload.get("reuse_primary_connection", True))
    model = _clean_optional_text(payload.get("model"))
    base_url = _clean_optional_text(payload.get("base_url"))
    protocol_override = _validate_protocol_override(payload.get("protocol_override"))

    if enabled and not model:
        raise SettingsValidationError("embedding.model is required when embedding is enabled")
    if reuse_primary_connection and protocol_override is not None:
        raise SettingsValidationError("embedding.protocol_override is not allowed when reusing the primary connection")
    if protocol_override not in (None, "openai_compatible"):
        raise SettingsValidationError("embedding.protocol_override currently only supports openai_compatible")

    return {
        "enabled": enabled,
        "model": model,
        "reuse_primary_connection": reuse_primary_connection,
        "base_url": base_url,
        "protocol_override": protocol_override,
    }


def _merge_secret(existing: dict[str, Any], api_key_input: Any) -> tuple[str | None, bool]:
    previous_ciphertext = _clean_optional_text(existing.get("api_key_ciphertext"))
    previous_is_encrypted = _to_bool(existing.get("api_key_is_encrypted"))
    if isinstance(api_key_input, str):
        if api_key_input == "":
            return None, False
        return encrypt_api_key(api_key_input.strip())
    return previous_ciphertext, previous_is_encrypted


def replace_model_settings(
    db: Session,
    *,
    primary_chat: dict[str, Any],
    embedding: dict[str, Any],
) -> None:
    existing = load_model_settings_db(db)

    normalized_primary = _normalize_primary_payload(dict(primary_chat or {}))
    primary_ciphertext, primary_is_encrypted = _merge_secret(existing.get("primary_chat", {}), primary_chat.get("api_key"))
    normalized_primary["api_key_ciphertext"] = primary_ciphertext
    normalized_primary["api_key_is_encrypted"] = primary_is_encrypted

    normalized_embedding = _normalize_embedding_payload(dict(embedding or {}))
    embedding_ciphertext, embedding_is_encrypted = _merge_secret(existing.get("embedding", {}), embedding.get("api_key"))
    normalized_embedding["api_key_ciphertext"] = embedding_ciphertext
    normalized_embedding["api_key_is_encrypted"] = embedding_is_encrypted

    rows = _load_setting_rows(db, [MODEL_SETTINGS_PRIMARY_KEY, MODEL_SETTINGS_EMBEDDING_KEY])

    primary_row = rows.get(MODEL_SETTINGS_PRIMARY_KEY)
    if primary_row is None:
        primary_row = SystemRuntimeSetting(setting_key=MODEL_SETTINGS_PRIMARY_KEY, setting_value_json=normalized_primary)
        db.add(primary_row)
    else:
        primary_row.setting_value_json = normalized_primary

    embedding_row = rows.get(MODEL_SETTINGS_EMBEDDING_KEY)
    if embedding_row is None:
        embedding_row = SystemRuntimeSetting(setting_key=MODEL_SETTINGS_EMBEDDING_KEY, setting_value_json=normalized_embedding)
        db.add(embedding_row)
    else:
        embedding_row.setting_value_json = normalized_embedding

    db.flush()


def list_runtime_overrides(db: Session) -> dict[str, Any]:
    rows = db.execute(select(SystemRuntimeSetting)).scalars().all()
    return {row.setting_key: row.setting_value_json for row in rows}


def set_runtime_overrides(db: Session, overrides: dict[str, Any]) -> None:
    updates = dict(overrides or {})
    unknown = [k for k in updates.keys() if k not in RUNTIME_SETTING_KEYS]
    if unknown:
        raise SettingsValidationError(f"unsupported runtime setting key(s): {', '.join(sorted(unknown))}")

    normalized_updates: dict[str, Any] = {}
    for key, value in updates.items():
        if value is None:
            normalized_updates[key] = None
            continue
        normalized_updates[key] = _coerce_runtime_value(key, value)

    existing = _load_setting_rows(db, list(normalized_updates.keys()))
    for key, value in normalized_updates.items():
        row = existing.get(key)
        if value is None:
            if row is not None:
                db.delete(row)
            continue
        if row is None:
            row = SystemRuntimeSetting(setting_key=key, setting_value_json=value)
            db.add(row)
        else:
            row.setting_value_json = value
    db.flush()
