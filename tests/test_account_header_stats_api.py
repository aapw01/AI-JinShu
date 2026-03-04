from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from app.core.authn import create_access_token
from app.core.database import SessionLocal
from app.models.novel import ChapterVersion, Novel, NovelVersion, User


def _ensure_user(user_uuid: str, role: str = "user") -> None:
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.uuid == user_uuid).one_or_none()
        if existing:
            return
        db.add(
            User(
                uuid=user_uuid,
                email=f"{user_uuid}@test.local",
                password_hash="x",
                role=role,
                status="active",
            )
        )
        db.commit()
    finally:
        db.close()


def _auth_headers(user_uuid: str, role: str = "user") -> dict[str, str]:
    _ensure_user(user_uuid, role=role)
    token = create_access_token(user_uuid, role=role, status="active")
    return {"Authorization": f"Bearer {token}"}


def _insert_novel_with_default_chapter(
    *,
    user_uuid: str,
    title: str,
    content: str,
    language_quality_score: float,
) -> None:
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        novel = Novel(
            user_id=user_uuid,
            title=title,
            target_language="zh",
            status="completed",
        )
        db.add(novel)
        db.flush()

        version = NovelVersion(
            novel_id=novel.id,
            version_no=1,
            status="completed",
            is_default=1,
        )
        db.add(version)
        db.flush()

        chapter = ChapterVersion(
            novel_version_id=version.id,
            chapter_num=1,
            title="第1章",
            content=content,
            status="completed",
            language_quality_score=language_quality_score,
            updated_at=now,
        )
        db.add(chapter)
        db.commit()
    finally:
        db.close()


def test_header_stats_scoped_to_current_user(anon_client):
    user_a = f"user-a-{uuid4().hex[:8]}"
    user_b = f"user-b-{uuid4().hex[:8]}"

    content_a = "甲" * 120
    content_b = "乙" * 300

    _insert_novel_with_default_chapter(
        user_uuid=user_a,
        title=f"Novel-{uuid4().hex[:8]}",
        content=content_a,
        language_quality_score=0.8,
    )
    _insert_novel_with_default_chapter(
        user_uuid=user_b,
        title=f"Novel-{uuid4().hex[:8]}",
        content=content_b,
        language_quality_score=0.2,
    )

    headers = _auth_headers(user_a, role="user")
    resp = anon_client.get("/api/account/header-stats", headers=headers)
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["works"] == 1
    assert payload["week_chapters"] == 1
    assert payload["total_words"] == len(content_a)
    assert payload["quality_score"] == 80.0


def test_header_stats_requires_auth(anon_client):
    resp = anon_client.get("/api/account/header-stats")
    assert resp.status_code == 401
