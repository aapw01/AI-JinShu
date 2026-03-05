from sqlalchemy import select

from app.core.authn import create_access_token
from app.core.database import SessionLocal
from app.core.authz.engine import authorize
from app.core.authz.types import Permission, Principal, ResourceContext
from app.models.novel import User


def _ensure_user(user_uuid: str, role: str = "user", status: str = "active") -> None:
    """Create a User row if it doesn't already exist, so JWT lookup succeeds."""
    db = SessionLocal()
    try:
        existing = db.execute(select(User).where(User.uuid == user_uuid)).scalar_one_or_none()
        if not existing:
            db.add(User(uuid=user_uuid, email=f"{user_uuid}@test.local", password_hash="x", role=role, status=status))
            db.commit()
    finally:
        db.close()


def _auth_headers(user_uuid: str, role: str = "user", status: str = "active") -> dict[str, str]:
    _ensure_user(user_uuid, role=role, status=status)
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


def test_admin_novel_filters(anon_client):
    owner_a_headers = _auth_headers("owner-a", role="user")
    owner_b_headers = _auth_headers("owner-b", role="user")
    admin_headers = _auth_headers("admin-x", role="admin")

    c1 = anon_client.post("/api/novels", json={"title": "owner-a-1", "target_language": "zh"}, headers=owner_a_headers)
    c2 = anon_client.post("/api/novels", json={"title": "owner-b-1", "target_language": "zh"}, headers=owner_b_headers)
    c3 = anon_client.post("/api/novels", json={"title": "admin-x-1", "target_language": "zh"}, headers=admin_headers)
    assert c1.status_code == 200
    assert c2.status_code == 200
    assert c3.status_code == 200

    admin_all = anon_client.get("/api/novels", headers=admin_headers)
    assert admin_all.status_code == 200
    all_titles = {x["title"] for x in admin_all.json()}
    assert "owner-a-1" in all_titles and "owner-b-1" in all_titles and "admin-x-1" in all_titles

    admin_only_mine = anon_client.get("/api/novels?only_mine=true", headers=admin_headers)
    assert admin_only_mine.status_code == 200
    only_mine_titles = {x["title"] for x in admin_only_mine.json()}
    assert "admin-x-1" in only_mine_titles
    assert "owner-a-1" not in only_mine_titles
    assert "owner-b-1" not in only_mine_titles

    admin_target = anon_client.get("/api/novels?user_uuid=owner-a", headers=admin_headers)
    assert admin_target.status_code == 200
    target_titles = {x["title"] for x in admin_target.json()}
    assert "owner-a-1" in target_titles
    assert "owner-b-1" not in target_titles


def test_non_admin_novel_filters_are_ignored(anon_client):
    owner_headers = _auth_headers("owner-c", role="user")
    other_headers = _auth_headers("owner-d", role="user")

    c1 = anon_client.post("/api/novels", json={"title": "owner-c-1", "target_language": "zh"}, headers=owner_headers)
    c2 = anon_client.post("/api/novels", json={"title": "owner-d-1", "target_language": "zh"}, headers=other_headers)
    assert c1.status_code == 200
    assert c2.status_code == 200

    scoped = anon_client.get("/api/novels?only_mine=false&user_uuid=owner-d", headers=owner_headers)
    assert scoped.status_code == 200
    titles = {x["title"] for x in scoped.json()}
    assert "owner-c-1" in titles
    assert "owner-d-1" not in titles


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
    detail = r.json().get("detail")
    if isinstance(detail, dict):
        assert detail.get("message") == "Email not verified"
    else:
        assert detail == "Email not verified"

    monkeypatch.delenv("AUTH_REQUIRE_EMAIL_VERIFICATION", raising=False)
    config_mod._settings = None
