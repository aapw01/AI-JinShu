from unittest.mock import patch

from sqlalchemy import select

from app.core import config as config_mod
from app.core.database import SessionLocal
from app.models.novel import User
from app.services.security import hash_password


def _reset_settings():
    config_mod._settings = None


def test_register_login_without_email_verification(anon_client, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRE_EMAIL_VERIFICATION", "false")
    _reset_settings()
    try:
        r = anon_client.post("/api/auth/register", json={"email": "auth1@example.com", "password": "Strong#Pass123"})
        assert r.status_code == 200
        data = r.json()
        assert data.get("access_token")

        token = data["access_token"]
        me = anon_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["user"]["email"] == "auth1@example.com"

        login = anon_client.post("/api/auth/login", json={"email": "auth1@example.com", "password": "Strong#Pass123"})
        assert login.status_code == 200
        assert login.json()["user"]["email"] == "auth1@example.com"
    finally:
        monkeypatch.delenv("AUTH_REQUIRE_EMAIL_VERIFICATION", raising=False)
        _reset_settings()


def test_register_with_email_verification_pending(anon_client, monkeypatch):
    captured = {"link": ""}
    monkeypatch.setenv("AUTH_REQUIRE_EMAIL_VERIFICATION", "true")
    monkeypatch.setenv("SENDGRID_API_KEY", "sg-test-key")
    monkeypatch.setenv("SENDGRID_FROM_EMAIL", "noreply@example.com")
    _reset_settings()
    try:
        with patch("app.api.routes.auth.send_verify_email") as mock_send:
            def _capture(_, link: str):
                captured["link"] = link
                return True

            mock_send.side_effect = _capture
            r = anon_client.post("/api/auth/register", json={"email": "auth2@example.com", "password": "Strong#Pass123"})
            assert r.status_code == 200
            assert "激活" in r.json().get("message", "")
            assert captured["link"]

        db = SessionLocal()
        try:
            user = db.execute(select(User).where(User.email == "auth2@example.com")).scalar_one()
            assert user.status == "pending_activation"
        finally:
            db.close()
    finally:
        monkeypatch.delenv("AUTH_REQUIRE_EMAIL_VERIFICATION", raising=False)
        monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
        monkeypatch.delenv("SENDGRID_FROM_EMAIL", raising=False)
        _reset_settings()


def test_register_verification_required_without_sendgrid(anon_client, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRE_EMAIL_VERIFICATION", "true")
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    monkeypatch.delenv("SENDGRID_FROM_EMAIL", raising=False)
    _reset_settings()
    try:
        r = anon_client.post("/api/auth/register", json={"email": "auth3@example.com", "password": "Strong#Pass123"})
        assert r.status_code == 503
        assert "mail service is not configured" in r.json().get("detail", "")
    finally:
        monkeypatch.delenv("AUTH_REQUIRE_EMAIL_VERIFICATION", raising=False)
        _reset_settings()


def test_pending_user_login_stays_unverified_when_verification_disabled(anon_client, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRE_EMAIL_VERIFICATION", "false")
    _reset_settings()
    db = SessionLocal()
    try:
        user = User(
            email="pending-login@example.com",
            password_hash=hash_password("Strong#Pass123"),
            role="user",
            status="pending_activation",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    finally:
        db.close()

    try:
        login = anon_client.post("/api/auth/login", json={"email": "pending-login@example.com", "password": "Strong#Pass123"})
        assert login.status_code == 403
        assert "Email not verified" in login.json().get("detail", "")

        db2 = SessionLocal()
        try:
            saved = db2.execute(select(User).where(User.email == "pending-login@example.com")).scalar_one()
            assert saved.status == "pending_activation"
            assert saved.email_verified_at is None
        finally:
            db2.close()
    finally:
        monkeypatch.delenv("AUTH_REQUIRE_EMAIL_VERIFICATION", raising=False)
        _reset_settings()
