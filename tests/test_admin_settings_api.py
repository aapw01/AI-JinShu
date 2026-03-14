from sqlalchemy import delete, select

from app.core.authn import create_access_token
from app.core.database import SessionLocal
from app.models.novel import (
    SystemModelDefinition,
    SystemModelProvider,
    SystemRuntimeSetting,
    User,
)
from app.services.system_settings.runtime import invalidate_caches


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


def _auth_headers(user_uuid: str, role: str = "user", status: str = "active") -> dict[str, str]:
    db = SessionLocal()
    try:
        existing = db.execute(select(User).where(User.uuid == user_uuid)).scalar_one_or_none()
        if not existing:
            db.add(User(uuid=user_uuid, email=f"{user_uuid}@test.local", password_hash="x", role=role, status=status))
            db.commit()
    finally:
        db.close()
    token = create_access_token(user_uuid, role=role, status=status)
    return {"Authorization": f"Bearer {token}"}


def test_admin_settings_models_and_runtime_crud(client):
    _cleanup_settings_tables()

    r1 = client.get("/api/admin/settings/models")
    assert r1.status_code == 200
    body1 = r1.json()
    assert "primary_chat" in body1
    assert "embedding" in body1

    save_models = client.put(
        "/api/admin/settings/models",
        json={
            "primary_chat": {
                "provider": "openai",
                "model": "qwen-plus",
                "base_url": "http://127.0.0.1:1234/v1",
                "api_key": "sk-local-1",
                "protocol_override": "openai_compatible",
            },
            "embedding": {
                "enabled": True,
                "model": "text-embedding-3-small",
                "reuse_primary_connection": False,
                "base_url": "http://127.0.0.1:1234/v1",
                "api_key": "sk-embed-1",
                "protocol_override": "openai_compatible",
            },
        },
    )
    assert save_models.status_code == 200
    body2 = save_models.json()
    assert body2["primary_chat"]["provider"] == "openai"
    assert body2["primary_chat"]["model"] == "qwen-plus"
    assert body2["primary_chat"]["api_key_value"] is None
    assert body2["embedding"]["model"] == "text-embedding-3-small"

    with_secret = client.get("/api/admin/settings/models?include_secrets=true")
    assert with_secret.status_code == 200
    payload = with_secret.json()
    assert payload["primary_chat"]["api_key_value"] == "sk-local-1"
    assert payload["embedding"]["api_key_value"] == "sk-embed-1"

    r_runtime = client.put(
        "/api/admin/settings/runtime",
        json={
            "updates": {
                "creation_scheduler_enabled": True,
                "creation_default_max_concurrent_tasks": 5,
            }
        },
    )
    assert r_runtime.status_code == 200
    runtime_items = {item["key"]: item for item in r_runtime.json()["items"]}
    assert runtime_items["creation_default_max_concurrent_tasks"]["value"] == 5
    assert runtime_items["creation_default_max_concurrent_tasks"]["source"] == "db"

    reset_runtime = client.put(
        "/api/admin/settings/runtime",
        json={"updates": {"creation_default_max_concurrent_tasks": None}},
    )
    assert reset_runtime.status_code == 200
    runtime_items2 = {item["key"]: item for item in reset_runtime.json()["items"]}
    assert runtime_items2["creation_default_max_concurrent_tasks"]["source"] == "env"


def test_non_admin_cannot_access_system_settings(anon_client):
    _cleanup_settings_tables()
    headers = _auth_headers("normal-user-settings", role="user", status="active")

    r1 = anon_client.get("/api/admin/settings/models", headers=headers)
    assert r1.status_code == 403

    r2 = anon_client.put("/api/admin/settings/runtime", headers=headers, json={"updates": {"creation_scheduler_enabled": True}})
    assert r2.status_code == 403


def test_unauthenticated_cannot_access_system_settings(anon_client):
    _cleanup_settings_tables()

    r1 = anon_client.get("/api/admin/settings/models")
    assert r1.status_code == 401

    r2 = anon_client.get("/api/admin/settings/models?include_secrets=true")
    assert r2.status_code == 401


def test_invalid_runtime_override_rejected(client):
    _cleanup_settings_tables()

    bad = client.put(
        "/api/admin/settings/runtime",
        json={"updates": {"creation_default_max_concurrent_tasks": 0}},
    )
    assert bad.status_code == 400
    assert "must be >=" in str(bad.json().get("detail", ""))

    bad_bool = client.put(
        "/api/admin/settings/runtime",
        json={"updates": {"creation_scheduler_enabled": "maybe"}},
    )
    assert bad_bool.status_code == 400
    assert "invalid boolean value" in str(bad_bool.json().get("detail", ""))

    verify = client.get("/api/admin/settings/runtime")
    assert verify.status_code == 200
    row = {item["key"]: item for item in verify.json()["items"]}
    assert row["creation_default_max_concurrent_tasks"]["source"] == "env"
