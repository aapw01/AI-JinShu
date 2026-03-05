"""Persistence layer for admin-managed system settings."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.novel import (
    SystemModelDefinition,
    SystemModelProvider,
    SystemRuntimeSetting,
)
from app.services.system_settings.crypto import encrypt_api_key

MODEL_TYPES = ("chat", "embedding", "image", "video")
ADAPTER_TYPES = ("openai_compatible", "anthropic", "gemini")

RUNTIME_SETTING_KEYS = frozenset(
    {
        "creation_scheduler_enabled",
        "creation_default_max_concurrent_tasks",
        "creation_max_dispatch_batch",
        "creation_worker_lease_ttl_seconds",
        "creation_worker_heartbeat_seconds",
        "quota_enforce_concurrency_limit",
        "quota_free_monthly_chapter_limit",
        "quota_free_monthly_token_limit",
        "quota_admin_monthly_chapter_limit",
        "quota_admin_monthly_token_limit",
        "llm_output_max_schema_retries",
        "llm_output_max_provider_fallbacks",
        "llm_output_min_chars",
    }
)

RUNTIME_SETTING_RULES: dict[str, dict[str, Any]] = {
    "creation_scheduler_enabled": {"kind": "bool"},
    "creation_default_max_concurrent_tasks": {"kind": "int", "min": 1, "max": 1000},
    "creation_max_dispatch_batch": {"kind": "int", "min": 1, "max": 1000},
    "creation_worker_lease_ttl_seconds": {"kind": "int", "min": 5, "max": 86_400},
    "creation_worker_heartbeat_seconds": {"kind": "int", "min": 1, "max": 3_600},
    "quota_enforce_concurrency_limit": {"kind": "bool"},
    "quota_free_monthly_chapter_limit": {"kind": "int", "min": 0, "max": 1_000_000_000},
    "quota_free_monthly_token_limit": {"kind": "int", "min": 0, "max": 100_000_000_000_000},
    "quota_admin_monthly_chapter_limit": {"kind": "int", "min": 0, "max": 1_000_000_000},
    "quota_admin_monthly_token_limit": {"kind": "int", "min": 0, "max": 100_000_000_000_000},
    "llm_output_max_schema_retries": {"kind": "int", "min": 0, "max": 10},
    "llm_output_max_provider_fallbacks": {"kind": "int", "min": 0, "max": 10},
    "llm_output_min_chars": {"kind": "int", "min": 0, "max": 20_000},
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

    if kind == "enum":
        normalized = str(value or "").strip().lower()
        choices = rule.get("choices") if isinstance(rule.get("choices"), set) else set()
        if normalized in choices:
            return normalized
        raise SettingsValidationError(f"invalid enum value for {key}: {value!r}")

    if kind != "int":
        raise SettingsValidationError(f"unsupported runtime setting type for key: {key}")

    if isinstance(value, bool):
        raise SettingsValidationError(f"invalid value for {key}: expected integer, got bool")
    try:
        normalized = int(str(value).strip())
    except (TypeError, ValueError):
        raise SettingsValidationError(f"invalid integer value for {key}: {value!r}")

    min_value = rule.get("min")
    max_value = rule.get("max")
    if isinstance(min_value, int) and normalized < min_value:
        raise SettingsValidationError(f"{key} must be >= {min_value}")
    if isinstance(max_value, int) and normalized > max_value:
        raise SettingsValidationError(f"{key} must be <= {max_value}")
    return normalized


def list_model_settings_db(db: Session) -> list[dict[str, Any]]:
    providers = list(
        db.execute(
            select(SystemModelProvider).order_by(SystemModelProvider.priority.asc(), SystemModelProvider.id.asc())
        ).scalars().all()
    )
    if not providers:
        return []
    provider_ids = [p.id for p in providers]
    models = list(
        db.execute(
            select(SystemModelDefinition)
            .where(SystemModelDefinition.provider_id.in_(provider_ids))
            .order_by(SystemModelDefinition.provider_id.asc(), SystemModelDefinition.id.asc())
        ).scalars().all()
    )
    by_provider: dict[int, list[SystemModelDefinition]] = defaultdict(list)
    for row in models:
        by_provider[int(row.provider_id)].append(row)

    payload: list[dict[str, Any]] = []
    for provider in providers:
        payload.append(
            {
                "provider_key": provider.provider_key,
                "display_name": provider.display_name,
                "adapter_type": provider.adapter_type,
                "base_url": provider.base_url,
                "api_key_ciphertext": provider.api_key_ciphertext,
                "api_key_is_encrypted": _to_bool(provider.api_key_is_encrypted),
                "is_enabled": _to_bool(provider.is_enabled),
                "priority": int(provider.priority or 100),
                "models": [
                    {
                        "model_name": m.model_name,
                        "model_type": m.model_type,
                        "is_default": _to_bool(m.is_default),
                        "is_enabled": _to_bool(m.is_enabled),
                        "metadata": m.metadata_ if isinstance(m.metadata_, dict) else {},
                    }
                    for m in by_provider.get(provider.id, [])
                ],
            }
        )
    return payload


def _validate_model_payload(providers: list[dict[str, Any]]) -> None:
    seen_keys: set[str] = set()
    default_count = {k: 0 for k in MODEL_TYPES}

    for provider in providers:
        provider_key = _normalize_key(str(provider.get("provider_key") or ""))
        if not provider_key:
            raise SettingsValidationError("provider_key is required")
        if provider_key in seen_keys:
            raise SettingsValidationError(f"duplicated provider_key: {provider_key}")
        seen_keys.add(provider_key)

        adapter_type = _normalize_key(str(provider.get("adapter_type") or ""))
        if adapter_type not in ADAPTER_TYPES:
            raise SettingsValidationError(f"unsupported adapter_type: {adapter_type}")

        models = provider.get("models") if isinstance(provider.get("models"), list) else []
        seen_model_keys: set[tuple[str, str]] = set()
        for model in models:
            model_name = str(model.get("model_name") or "").strip()
            model_type = _normalize_key(str(model.get("model_type") or "chat"))
            if not model_name:
                raise SettingsValidationError(f"model_name is required for provider: {provider_key}")
            if model_type not in MODEL_TYPES:
                raise SettingsValidationError(f"unsupported model_type: {model_type}")
            uniq_key = (model_name, model_type)
            if uniq_key in seen_model_keys:
                raise SettingsValidationError(
                    f"duplicated model_name/model_type in provider {provider_key}: {model_name}/{model_type}"
                )
            seen_model_keys.add(uniq_key)
            if _to_bool(model.get("is_default")):
                default_count[model_type] += 1

    for model_type, count in default_count.items():
        if count > 1:
            raise SettingsValidationError(f"multiple default models configured for model_type={model_type}")


def replace_model_settings(db: Session, providers: list[dict[str, Any]]) -> None:
    normalized = list(providers or [])
    _validate_model_payload(normalized)

    existing = {
        row.provider_key: row
        for row in db.execute(select(SystemModelProvider)).scalars().all()
    }

    db.execute(delete(SystemModelDefinition))
    db.execute(delete(SystemModelProvider))
    db.flush()

    for item in normalized:
        provider_key = _normalize_key(str(item.get("provider_key") or ""))
        old = existing.get(provider_key)

        api_key_input = item.get("api_key")
        api_key_ciphertext = old.api_key_ciphertext if old else None
        api_key_is_encrypted = _to_bool(old.api_key_is_encrypted) if old else False
        if isinstance(api_key_input, str):
            if api_key_input.strip() == "":
                api_key_ciphertext = None
                api_key_is_encrypted = False
            else:
                api_key_ciphertext, api_key_is_encrypted = encrypt_api_key(api_key_input.strip())
        elif api_key_input is None:
            # keep previous secret for same provider key
            pass

        provider_row = SystemModelProvider(
            provider_key=provider_key,
            display_name=str(item.get("display_name") or provider_key),
            adapter_type=_normalize_key(str(item.get("adapter_type") or "openai_compatible")),
            base_url=(str(item.get("base_url") or "").strip() or None),
            api_key_ciphertext=api_key_ciphertext,
            api_key_is_encrypted=1 if api_key_is_encrypted else 0,
            is_enabled=1 if _to_bool(item.get("is_enabled", True)) else 0,
            priority=int(item.get("priority") or 100),
        )
        db.add(provider_row)
        db.flush()

        for model in (item.get("models") if isinstance(item.get("models"), list) else []):
            db.add(
                SystemModelDefinition(
                    provider_id=provider_row.id,
                    model_name=str(model.get("model_name") or "").strip(),
                    model_type=_normalize_key(str(model.get("model_type") or "chat")),
                    is_default=1 if _to_bool(model.get("is_default")) else 0,
                    is_enabled=1 if _to_bool(model.get("is_enabled", True)) else 0,
                    metadata_=model.get("metadata") if isinstance(model.get("metadata"), dict) else {},
                )
            )

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

    existing = {
        row.setting_key: row
        for row in db.execute(
            select(SystemRuntimeSetting).where(SystemRuntimeSetting.setting_key.in_(list(normalized_updates.keys())))
        )
        .scalars()
        .all()
    }

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
