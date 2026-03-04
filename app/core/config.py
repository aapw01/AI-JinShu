"""Application configuration."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings from environment."""

    database_url: str = "postgresql://novel:novel_secret@localhost:5432/novel_db"
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    default_llm_provider: str = "openai"
    default_llm_model: str = "gpt-4o-mini"
    default_embedding_model: str = "text-embedding-3-small"
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    # Backward-compatible provider-specific fields.
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    anthropic_api_key: str | None = None
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_api_key: str | None = None
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
    quota_enforce_concurrency_limit: bool = False
    creation_scheduler_enabled: bool = True
    creation_default_max_concurrent_tasks: int = 1
    creation_dispatch_poll_seconds: int = 2
    creation_max_dispatch_batch: int = 5
    creation_worker_lease_ttl_seconds: int = 300
    creation_worker_heartbeat_seconds: int = 30
    creation_recovery_poll_seconds: int = 5
    quota_free_monthly_chapter_limit: int = 1_000_000
    quota_free_monthly_token_limit: int = 10_000_000_000
    quota_admin_monthly_chapter_limit: int = 10_000_000
    quota_admin_monthly_token_limit: int = 100_000_000_000
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
