from sqlalchemy import select

from app.core.authn import create_access_token
from app.core.database import SessionLocal
from app.core.authz.engine import authorize
from app.core.authz.types import Permission, Principal, ResourceContext
from app.models.novel import User


def _auth_headers(user_uuid: str, role: str = "user", status: str = "active") -> dict[str, str]:
    token = create_access_token(user_uuid, role=role, status=status)
    return {"Authorization": f"Bearer {token}"}


def test_authz_engine_owner_and_admin_paths():
    owner = Principal(user_uuid="u1", role="user", status="active", is_authenticated=True)
    other = Principal(user_uuid="u2", role="user", status="active", is_authenticated=True)
    admin = Principal(user_uuid="a1", role="admin", status="active", is_authenticated=True)
    resource = ResourceContext(resource_type="novel", resource_id="n1", owner_uuid="u1")

    assert authorize(owner, Permission.NOVEL_READ, resource).allowed is True
    assert authorize(other, Permission.NOVEL_READ, resource).allowed is False
    assert authorize(admin, Permission.NOVEL_READ, resource).allowed is True


def test_authz_engine_disabled_user_denied():
    disabled = Principal(user_uuid="u1", role="user", status="disabled", is_authenticated=True)
    result = authorize(disabled, Permission.NOVEL_READ, ResourceContext("novel", "n1", "u1"))
    assert result.allowed is False


def test_anonymous_api_protection(anon_client):
    r = anon_client.get("/api/novels")
    assert r.status_code == 401


def test_owner_scope_and_admin_visibility(anon_client):
    owner_headers = _auth_headers("owner-1", role="user")
    other_headers = _auth_headers("owner-2", role="user")
    admin_headers = _auth_headers("admin-1", role="admin")

    n1 = anon_client.post("/api/novels", json={"title": "n1", "target_language": "zh"}, headers=owner_headers)
    n2 = anon_client.post("/api/novels", json={"title": "n2", "target_language": "zh"}, headers=other_headers)
    assert n1.status_code == 200
    assert n2.status_code == 200

    owner_list = anon_client.get("/api/novels", headers=owner_headers)
    assert owner_list.status_code == 200
    assert len(owner_list.json()) == 1
    assert owner_list.json()[0]["title"] == "n1"

    admin_list = anon_client.get("/api/novels", headers=admin_headers)
    assert admin_list.status_code == 200
    titles = {x["title"] for x in admin_list.json()}
    assert "n1" in titles and "n2" in titles


def test_admin_users_endpoint_requires_admin(anon_client):
    user_headers = _auth_headers("normal-user", role="user")
    r = anon_client.get("/api/admin/users", headers=user_headers)
    assert r.status_code == 403

    admin_headers = _auth_headers("admin-user", role="admin")
    ok = anon_client.get("/api/admin/users", headers=admin_headers)
    assert ok.status_code == 200


def test_disabled_user_blocked_by_authn(anon_client):
    db = SessionLocal()
    try:
        user = User(email="disabled@example.com", password_hash="x", role="user", status="disabled")
        db.add(user)
        db.commit()
        db.refresh(user)
        headers = _auth_headers(user.uuid, role="user", status="active")
    finally:
        db.close()

    # DB status should override token claim status and reject.
    r = anon_client.get("/api/novels", headers=headers)
    assert r.status_code == 401


def test_pending_activation_still_blocked_when_verification_toggle_off(anon_client, monkeypatch):
    from app.core import config as config_mod

    monkeypatch.setenv("AUTH_REQUIRE_EMAIL_VERIFICATION", "false")
    config_mod._settings = None
    db = SessionLocal()
    try:
        user = User(email="pending@example.com", password_hash="x", role="user", status="pending_activation")
        db.add(user)
        db.commit()
        db.refresh(user)
        headers = _auth_headers(user.uuid, role="user", status="pending_activation")
    finally:
        db.close()

    r = anon_client.get("/api/novels", headers=headers)
    assert r.status_code == 401
    assert r.json().get("detail") == "Email not verified"

    monkeypatch.delenv("AUTH_REQUIRE_EMAIL_VERIFICATION", raising=False)
    config_mod._settings = None
