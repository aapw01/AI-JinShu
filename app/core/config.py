"""Application configuration."""
import os

from pydantic_settings import BaseSettings, SettingsConfigDict


_DEPRECATED_MODEL_ENV_KEYS = (
    "DEFAULT_LLM_PROVIDER",
    "DEFAULT_LLM_MODEL",
    "LLM_ADAPTER_TYPE",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "GEMINI_API_KEY",
    "GEMINI_BASE_URL",
)


class Settings(BaseSettings):
    """Application settings from environment."""

    database_url: str = "postgresql://novel:novel_secret@localhost:5432/novel_db"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    # Force a specific wire protocol: openai_compatible | gemini | anthropic
    # Leave empty for auto-inference (custom base_url → openai_compatible, else provider native).
    llm_protocol_override: str | None = None
    embedding_enabled: bool = True
    embedding_model: str = "text-embedding-3-small"
    embedding_reuse_primary_connection: bool = True
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    auth_jwt_secret: str = "change-me-in-production-and-must-be-at-least-32-bytes"
    auth_jwt_issuer: str = "ai-jinshu"
    auth_access_token_minutes: int = 15
    auth_cookie_secure: bool = False
    auth_cookie_domain: str | None = None
    auth_require_email_verification: bool = False
    auth_login_max_failures: int = 5
    auth_login_lock_minutes: int = 15
    auth_verify_token_minutes: int = 60 * 24
    auth_reset_token_minutes: int = 30
    auth_frontend_base_url: str = "http://localhost:3000"
    sendgrid_api_key: str | None = None
    sendgrid_from_email: str | None = None
    creation_scheduler_enabled: bool = True
    creation_default_max_concurrent_tasks: int = 1
    log_format: str = "json"
    log_level: str = "INFO"
    log_slow_threshold_ms: int = 1500
    log_node_slow_threshold_ms: int = 2500
    log_redaction_level: str = "minimal"
    # Optional master key for encrypting system settings secrets (API keys).
    system_settings_master_key: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


_settings: Settings | None = None

_JWT_SECRET_DEFAULTS = frozenset({
    "change-me-in-production-and-must-be-at-least-32-bytes",
    "change-me",
    "",
})


def get_settings() -> Settings:
    """Get cached settings instance."""
    global _settings
    if _settings is None:
        bad = [key for key in _DEPRECATED_MODEL_ENV_KEYS if os.getenv(key)]
        if bad:
            raise RuntimeError(
                "Deprecated model environment variable(s) detected: "
                f"{', '.join(sorted(bad))}. "
                "Use only: LLM_PROVIDER, LLM_MODEL, LLM_BASE_URL, LLM_API_KEY, "
                "EMBEDDING_ENABLED, EMBEDDING_MODEL, EMBEDDING_REUSE_PRIMARY_CONNECTION, "
                "EMBEDDING_BASE_URL, EMBEDDING_API_KEY."
            )
        _settings = Settings()
    return _settings


def validate_settings_for_production() -> None:
    """Called at app startup to reject insecure defaults in non-dev environments."""
    import os
    if os.getenv("ENV", "development").lower() in ("development", "test"):
        return
    s = get_settings()
    if s.auth_jwt_secret in _JWT_SECRET_DEFAULTS or len(s.auth_jwt_secret) < 32:
        raise RuntimeError(
            "AUTH_JWT_SECRET must be explicitly set to a strong value (>= 32 chars) in production."
        )
