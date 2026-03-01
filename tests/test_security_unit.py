from datetime import timezone

from app.services import security


def test_password_complexity_and_weak_password():
    ok, _ = security.validate_password_complexity("StrongPass!234")
    assert ok is True

    ok2, msg2 = security.validate_password_complexity("weak")
    assert ok2 is False
    assert "至少10位" in msg2

    ok3, msg3 = security.validate_password_complexity("12345678")
    assert ok3 is False
    assert "至少10位" in msg3


def test_hash_and_verify_password():
    encoded = security.hash_password("MyStrong!Pass123")
    assert encoded.startswith("pbkdf2_sha256$")
    assert security.verify_password("MyStrong!Pass123", encoded) is True
    assert security.verify_password("WrongPass!123", encoded) is False
    assert security.verify_password("Any", "bad-format") is False


def test_token_and_links(monkeypatch):
    t = security.new_raw_token()
    assert isinstance(t, str) and len(t) >= 20
    assert len(security.token_hash(t)) == 64

    dt = security.future_minutes(0)
    assert dt.tzinfo == timezone.utc
    assert dt >= security.utc_now()

    class _S:
        auth_frontend_base_url = "http://localhost:3000/"

    monkeypatch.setattr(security, "get_settings", lambda: _S())
    assert security.build_verify_link("abc").endswith("/auth/verify?token=abc")
    assert security.build_reset_link("abc").endswith("/auth/forgot-password?token=abc")
