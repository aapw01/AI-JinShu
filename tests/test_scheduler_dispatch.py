from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal, resolve_novel
from app.models.creation_task import CreationTask
from app.models.novel import NovelVersion, User
from app.services.generation.status_snapshot import generation_task_key, read_generation_cache
from app.services.quota import ensure_user_quota
from app.services.scheduler.scheduler_service import (
    dispatch_user_queue,
    dispatch_user_queue_for_user,
    mark_task_running,
    repair_active_dispatching_tasks,
    submit_task,
)


def _default_version_id(novel_public_id: str) -> int:
    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_public_id)
        assert novel is not None
        row = db.execute(
            select(NovelVersion).where(
                NovelVersion.novel_id == novel.id,
                NovelVersion.is_default == 1,
            )
        ).scalar_one()
        return int(row.id)
    finally:
        db.close()


def test_dispatch_user_queue_ignores_uncommitted_generation_task(client, monkeypatch):
    created = client.post("/api/novels", json={"title": "Dispatch Commit", "target_language": "zh"})
    assert created.status_code == 200
    novel_public_id = created.json()["id"]
    version_id = _default_version_id(novel_public_id)

    quota_db = SessionLocal()
    try:
        user = quota_db.execute(select(User).where(User.uuid == "test-admin-user")).scalar_one()
        ensure_user_quota(quota_db, user)
        quota_db.commit()
    finally:
        quota_db.close()

    from app.tasks.generation import submit_generation_task

    calls: list[str] = []

    def _fake_apply_async(*, args=None, kwargs=None, task_id=None, **_extra):
        calls.append(str(task_id))
        return SimpleNamespace(id=task_id)

    monkeypatch.setattr(submit_generation_task, "apply_async", _fake_apply_async)

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_public_id)
        assert novel is not None
        task = submit_task(
            db,
            user_uuid="test-admin-user",
            task_type="generation",
            resource_type="novel",
            resource_id=int(novel.id),
            payload={
                "novel_id": int(novel.id),
                "novel_version_id": int(version_id),
                "num_chapters": 10,
                "start_chapter": 1,
                "book_start_chapter": 1,
                "book_target_total_chapters": 10,
            },
        )
        dispatched = dispatch_user_queue(db, user_uuid="test-admin-user")
        assert dispatched == []
        assert task.worker_task_id is None
        assert calls == []
        db.commit()
        task_public_id = task.public_id
    finally:
        db.close()

    dispatched = dispatch_user_queue_for_user(user_uuid="test-admin-user")
    assert len(dispatched) == 1
    assert len(calls) == 1

    db = SessionLocal()
    try:
        row = db.execute(select(CreationTask).where(CreationTask.public_id == task_public_id)).scalar_one()
        assert row.status == "dispatching"
        assert row.worker_task_id == calls[0]
        snapshot = read_generation_cache(generation_task_key(task_public_id))
        assert snapshot is not None
        assert snapshot["status"] == "dispatching"
    finally:
        db.close()


def test_mark_task_running_requires_dispatching_owner():
    db = SessionLocal()
    try:
        row = CreationTask(
            user_uuid="test-admin-user",
            task_type="rewrite",
            resource_type="rewrite_request",
            resource_id=1,
            status="dispatching",
            worker_task_id="worker-123",
        )
        db.add(row)
        db.commit()
        db.refresh(row)

        with pytest.raises(ValueError, match="worker_not_owner"):
            mark_task_running(db, task_id=int(row.id), worker_task_id="worker-999")

        running = mark_task_running(db, task_id=int(row.id), worker_task_id="worker-123")
        assert running.status == "running"
    finally:
        db.close()


def test_repair_active_dispatching_tasks_promotes_running_generation_snapshot(client):
    created = client.post("/api/novels", json={"title": "Repair Active Dispatching", "target_language": "zh"})
    assert created.status_code == 200
    novel_public_id = created.json()["id"]
    version_id = _default_version_id(novel_public_id)

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_public_id)
        assert novel is not None
        row = CreationTask(
            user_uuid="test-admin-user",
            task_type="generation",
            resource_type="novel",
            resource_id=int(novel.id),
            status="dispatching",
            phase="memory_update",
            progress=22.5,
            message="正在更新记忆",
            payload_json={
                "novel_id": int(novel.id),
                "novel_version_id": int(version_id),
                "num_chapters": 200,
                "start_chapter": 1,
                "book_start_chapter": 1,
                "book_target_total_chapters": 200,
            },
            worker_task_id="worker-active-1",
            last_heartbeat_at=now,
            worker_lease_expires_at=now + timedelta(minutes=5),
        )
        db.add(row)
        db.commit()
        db.refresh(row)

        repaired = repair_active_dispatching_tasks(db)
        db.commit()
        assert repaired == 1

        db.refresh(row)
        assert row.status == "running"
        snapshot = read_generation_cache(generation_task_key(row.public_id))
        assert snapshot is not None
        assert snapshot["status"] == "running"
        assert snapshot["current_phase"] == "memory_update"
    finally:
        db.close()
