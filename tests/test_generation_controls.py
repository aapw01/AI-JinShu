from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.creation_task import CreationTask
from app.models.novel import GenerationTask, Novel, NovelVersion, User
from app.services.quota import ensure_user_quota


def test_generation_pause_resume_cancel_and_list(client):
    r = client.post("/api/novels", json={"title": "Control Test", "target_language": "zh"})
    assert r.status_code == 200
    novel_id = r.json()["id"]

    s = client.post(
        f"/api/novels/{novel_id}/generate",
        json={"num_chapters": 3, "start_chapter": 1, "idempotency_key": "idem-1"},
    )
    assert s.status_code == 200
    task_id = s.json()["task_id"]
    assert task_id

    tasks = client.get(f"/api/novels/{novel_id}/generation/tasks")
    assert tasks.status_code == 200
    assert len(tasks.json()) >= 1
    assert any(t["task_id"] == task_id for t in tasks.json())

    pause = client.post(f"/api/novels/{novel_id}/generation/pause?task_id={task_id}")
    assert pause.status_code == 200
    assert pause.json()["run_state"] == "paused"

    resume = client.post(f"/api/novels/{novel_id}/generation/resume?task_id={task_id}")
    assert resume.status_code == 200
    assert resume.json()["run_state"] == "queued"

    cancel = client.post(f"/api/novels/{novel_id}/generation/cancel?task_id={task_id}")
    assert cancel.status_code == 200
    assert cancel.json()["run_state"] == "cancelled"


def test_generation_retry_keeps_source_novel_version(client):
    created = client.post("/api/novels", json={"title": "Retry Version Lock", "target_language": "zh"})
    assert created.status_code == 200
    novel_public_id = created.json()["id"]

    submit = client.post(
        f"/api/novels/{novel_public_id}/generate",
        json={"num_chapters": 3, "start_chapter": 1, "idempotency_key": "idem-retry-version-lock"},
    )
    assert submit.status_code == 200
    source_public_task_id = submit.json()["task_id"]

    db = SessionLocal()
    try:
        novel = db.execute(select(Novel).where(Novel.uuid == novel_public_id)).scalar_one()
        source_creation = db.execute(
            select(CreationTask).where(
                CreationTask.public_id == source_public_task_id,
                CreationTask.task_type == "generation",
                CreationTask.resource_id == novel.id,
            )
        ).scalar_one()
        source_version_id = int((source_creation.payload_json or {}).get("novel_version_id"))

        source_gt = db.execute(select(GenerationTask).where(GenerationTask.task_id == source_public_task_id)).scalar_one_or_none()
        if source_gt is None:
            source_gt = GenerationTask(
                task_id=source_public_task_id,
                novel_id=novel.id,
                status="failed",
                run_state="failed",
                current_chapter=2,
                total_chapters=3,
                num_chapters=3,
                start_chapter=1,
                message="seed failed task",
            )
            db.add(source_gt)
        else:
            source_gt.status = "failed"
            source_gt.current_chapter = 2
            source_gt.total_chapters = 3
            source_gt.start_chapter = 1
            source_gt.run_state = "failed"

        next_version_no = (
            db.execute(select(NovelVersion.version_no).where(NovelVersion.novel_id == novel.id).order_by(NovelVersion.version_no.desc()))
            .scalars()
            .first()
            or 1
        ) + 1
        for v in db.execute(select(NovelVersion).where(NovelVersion.novel_id == novel.id)).scalars().all():
            v.is_default = 0
        db.add(NovelVersion(novel_id=novel.id, version_no=next_version_no, status="completed", is_default=1))
        db.commit()
    finally:
        db.close()

    retry = client.post(f"/api/novels/{novel_public_id}/generation/retry", json={})
    assert retry.status_code == 200
    retry_public_task_id = retry.json()["task_id"]

    db = SessionLocal()
    try:
        novel = db.execute(select(Novel).where(Novel.uuid == novel_public_id)).scalar_one()
        retry_creation = db.execute(
            select(CreationTask).where(
                CreationTask.public_id == retry_public_task_id,
                CreationTask.task_type == "generation",
                CreationTask.resource_id == novel.id,
            )
        ).scalar_one()
        assert int((retry_creation.payload_json or {}).get("novel_version_id")) == source_version_id
    finally:
        db.close()


def test_generation_quota_error_returns_structured_message(client):
    created = client.post("/api/novels", json={"title": "Quota Block", "target_language": "zh"})
    assert created.status_code == 200
    novel_public_id = created.json()["id"]

    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.uuid == "test-admin-user")).scalar_one_or_none()
        if not user:
            user = User(email="quota-test@example.com", password_hash="x", role="admin", status="active")
            user.uuid = "test-admin-user"
            db.add(user)
            db.flush()
        quota = ensure_user_quota(db, user)
        quota.monthly_token_limit = 0
        db.commit()
    finally:
        db.close()

    blocked = client.post(
        f"/api/novels/{novel_public_id}/generate",
        json={"num_chapters": 1, "start_chapter": 1, "idempotency_key": "idem-quota-block"},
    )
    assert blocked.status_code == 429
    detail = blocked.json().get("detail", {})
    assert detail.get("error_code") == "monthly_token_limit_exceeded"
    assert "token" in str(detail.get("message") or "")
