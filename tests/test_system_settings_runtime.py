from sqlalchemy import delete

from app.core.database import SessionLocal
from app.models.novel import SystemModelDefinition, SystemModelProvider, SystemRuntimeSetting
from app.services.system_settings.repository import replace_model_settings, set_runtime_overrides
from app.services.system_settings.runtime import (
    get_default_model_for_type,
    get_effective_model_config,
    get_effective_runtime_setting,
    get_enabled_provider_order,
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


def test_model_defaults_and_fallback_order_from_db():
    _cleanup_settings_tables()
    db = SessionLocal()
    try:
        replace_model_settings(
            db,
            providers=[
                {
                    "provider_key": "provider_a",
                    "display_name": "Provider A",
                    "adapter_type": "openai_compatible",
                    "base_url": "http://localhost:1234/v1",
                    "api_key": "sk-a",
                    "is_enabled": True,
                    "priority": 1,
                    "models": [
                        {
                            "model_name": "chat-a",
                            "model_type": "chat",
                            "is_default": True,
                            "is_enabled": True,
                        },
                        {
                            "model_name": "emb-a",
                            "model_type": "embedding",
                            "is_default": True,
                            "is_enabled": True,
                        },
                    ],
                },
                {
                    "provider_key": "provider_b",
                    "display_name": "Provider B",
                    "adapter_type": "openai_compatible",
                    "base_url": "http://localhost:5678/v1",
                    "api_key": "sk-b",
                    "is_enabled": True,
                    "priority": 2,
                    "models": [
                        {
                            "model_name": "chat-b",
                            "model_type": "chat",
                            "is_default": False,
                            "is_enabled": True,
                        }
                    ],
                },
            ],
        )
        db.commit()
    finally:
        db.close()

    invalidate_caches()
    defaults_chat = get_default_model_for_type("chat")
    defaults_embedding = get_default_model_for_type("embedding")
    assert defaults_chat and defaults_chat["provider_key"] == "provider_a"
    assert defaults_chat["model_name"] == "chat-a"
    assert defaults_embedding and defaults_embedding["model_name"] == "emb-a"

    order = get_enabled_provider_order()
    assert order[0] == "provider_a"
    assert order[1] == "provider_b"

    cfg = get_effective_model_config()
    assert cfg["security_mode"] in {"encrypted", "plaintext"}


def test_env_unified_provider_only_exposes_selected_provider(monkeypatch):
    _cleanup_settings_tables()
    monkeypatch.setenv("DEFAULT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1234/v1")
    monkeypatch.setenv("LLM_API_KEY", "sk-only-openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    from app.core import config as config_mod

    config_mod._settings = None
    invalidate_caches()
    cfg = get_effective_model_config()
    providers = cfg.get("providers", [])
    assert len(providers) == 1
    assert providers[0]["provider_key"] == "openai"

    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    config_mod._settings = None
    invalidate_caches()

