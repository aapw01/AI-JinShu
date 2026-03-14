from sqlalchemy import delete

from app.core.database import SessionLocal
from app.models.novel import SystemModelDefinition, SystemModelProvider, SystemRuntimeSetting
from app.services.system_settings.repository import replace_model_settings, set_runtime_overrides
from app.services.system_settings.runtime import (
    get_default_model_for_type,
    get_effective_model_config,
    get_effective_runtime_setting,
    invalidate_caches,
)


def _cleanup_settings_tables() -> None:
    db = SessionLocal()
    try:
        db.execute(delete(SystemModelDefinition))
        db.execute(delete(SystemModelProvider))
        db.execute(delete(SystemRuntimeSetting))
        db.commit()
    finally:
        db.close()
    invalidate_caches()


def test_runtime_setting_prefers_db_over_env(monkeypatch):
    _cleanup_settings_tables()
    db = SessionLocal()
    try:
        set_runtime_overrides(db, {"creation_default_max_concurrent_tasks": 7})
        db.commit()
    finally:
        db.close()

    assert get_effective_runtime_setting("creation_default_max_concurrent_tasks", int, 1) == 7

    _cleanup_settings_tables()
    monkeypatch.setenv("CREATION_DEFAULT_MAX_CONCURRENT_TASKS", "3")
    from app.core import config as config_mod

    config_mod._settings = None
    assert get_effective_runtime_setting("creation_default_max_concurrent_tasks", int, 1) == 3
    monkeypatch.delenv("CREATION_DEFAULT_MAX_CONCURRENT_TASKS", raising=False)
    config_mod._settings = None


def test_model_defaults_from_db():
    _cleanup_settings_tables()
    db = SessionLocal()
    try:
        replace_model_settings(
            db,
            primary_chat={
                "provider": "gemini",
                "model": "gemini-2.5-pro",
                "base_url": None,
                "api_key": "sk-primary",
                "protocol_override": None,
            },
            embedding={
                "enabled": True,
                "model": "text-embedding-3-small",
                "reuse_primary_connection": False,
                "base_url": "http://localhost:5678/v1",
                "api_key": "sk-embed",
                "protocol_override": "openai_compatible",
            },
        )
        db.commit()
    finally:
        db.close()

    invalidate_caches()
    defaults_chat = get_default_model_for_type("chat")
    defaults_embedding = get_default_model_for_type("embedding")
    assert defaults_chat and defaults_chat["provider_key"] == "gemini"
    assert defaults_chat["model_name"] == "gemini-2.5-pro"
    assert defaults_embedding and defaults_embedding["model_name"] == "text-embedding-3-small"

    cfg = get_effective_model_config()
    assert cfg["primary_chat"]["provider"] == "gemini"
    assert cfg["embedding"]["reuse_primary_connection"] is False
    assert cfg["security_mode"] in {"encrypted", "plaintext"}


def test_env_model_settings_use_single_primary_and_embedding(monkeypatch):
    _cleanup_settings_tables()
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-4.1-mini")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-only-openai")
    monkeypatch.setenv("LLM_PROTOCOL_OVERRIDE", "")
    monkeypatch.setenv("EMBEDDING_ENABLED", "true")
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setenv("EMBEDDING_REUSE_PRIMARY_CONNECTION", "true")
    from app.core import config as config_mod

    config_mod._settings = None
    invalidate_caches()
    cfg = get_effective_model_config()
    assert cfg["primary_chat"]["provider"] == "openai"
    assert cfg["primary_chat"]["resolved_protocol"] == "openai_compatible"
    assert cfg["embedding"]["enabled"] is True
    assert cfg["embedding"]["reuse_primary_connection"] is True

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_ENABLED", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("EMBEDDING_REUSE_PRIMARY_CONNECTION", raising=False)
    config_mod._settings = None
    invalidate_caches()


def test_deprecated_model_env_keys_fail_fast(monkeypatch):
    monkeypatch.setenv("DEFAULT_LLM_PROVIDER", "openai")
    from app.core import config as config_mod

    config_mod._settings = None
    import pytest

    with pytest.raises(RuntimeError, match="Deprecated model environment variable"):
        config_mod.get_settings()

    monkeypatch.delenv("DEFAULT_LLM_PROVIDER", raising=False)
    config_mod._settings = None
