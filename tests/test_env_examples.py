from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _parse_env_keys(path: Path) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        pairs[key.strip()] = value
    return pairs


def test_backend_env_example_tracks_live_settings():
    env_keys = _parse_env_keys(ROOT / ".env.example")

    expected = {
        "ENV",
        "DATABASE_URL",
        "REDIS_URL",
        "CELERY_BROKER_URL",
        "LLM_PROVIDER",
        "LLM_MODEL",
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_PROTOCOL_OVERRIDE",
        "EMBEDDING_ENABLED",
        "EMBEDDING_MODEL",
        "EMBEDDING_REUSE_PRIMARY_CONNECTION",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_API_KEY",
        "AUTH_JWT_SECRET",
        "AUTH_JWT_ISSUER",
        "AUTH_ACCESS_TOKEN_MINUTES",
        "AUTH_COOKIE_SECURE",
        "AUTH_COOKIE_DOMAIN",
        "AUTH_REQUIRE_EMAIL_VERIFICATION",
        "AUTH_LOGIN_MAX_FAILURES",
        "AUTH_LOGIN_LOCK_MINUTES",
        "AUTH_VERIFY_TOKEN_MINUTES",
        "AUTH_RESET_TOKEN_MINUTES",
        "AUTH_FRONTEND_BASE_URL",
        "SENDGRID_API_KEY",
        "SENDGRID_FROM_EMAIL",
        "CREATION_SCHEDULER_ENABLED",
        "CREATION_DEFAULT_MAX_CONCURRENT_TASKS",
        "LOG_FORMAT",
        "LOG_LEVEL",
        "LOG_SLOW_THRESHOLD_MS",
        "LOG_NODE_SLOW_THRESHOLD_MS",
        "LOG_REDACTION_LEVEL",
        "LOG_DIR",
        "SYSTEM_SETTINGS_MASTER_KEY",
    }

    assert expected.issubset(env_keys.keys())
    assert "DEFAULT_LLM_PROVIDER" not in env_keys
    assert env_keys["DATABASE_URL"].endswith("@localhost:25432/novel_db")
    assert env_keys["REDIS_URL"] == "redis://localhost:26379/0"


def test_web_env_example_includes_internal_api_target():
    env_keys = _parse_env_keys(ROOT / "web/.env.example")

    assert env_keys["NEXT_PUBLIC_API_URL"] == "http://localhost:8000"
    assert env_keys["API_TARGET_URL"] == "http://localhost:8000"
