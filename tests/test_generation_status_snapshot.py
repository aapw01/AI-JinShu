from __future__ import annotations

import redis

from app.core.database import SessionLocal
from app.models.creation_task import CreationTask
from app.services.generation.status_snapshot import (
    delete_generation_novel_cache,
    delete_generation_worker_cache,
    generation_novel_key,
    generation_task_key,
    read_generation_cache,
)


class _BrokenRedis:
    def get(self, _key):
        raise redis.RedisError("redis unavailable")

    def setex(self, _key, _ttl, _value):
        raise redis.RedisError("redis unavailable")

    def delete(self, _key):
        raise redis.RedisError("redis unavailable")


def test_generation_cache_reads_from_db_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(
        "app.services.generation.status_snapshot.get_generation_redis",
        lambda: _BrokenRedis(),
    )

    db = SessionLocal()
    try:
        row = CreationTask(
            user_uuid="test-admin-user",
            task_type="generation",
            resource_type="novel",
            resource_id=9901,
            status="dispatching",
            phase="dispatching",
            message="任务调度中",
            worker_task_id="worker-db-1",
            payload_json={
                "novel_id": 9901,
                "novel_version_id": 1,
                "num_chapters": 10,
                "start_chapter": 1,
                "book_start_chapter": 1,
                "book_target_total_chapters": 10,
            },
        )
        db.add(row)
        db.commit()
        db.refresh(row)

        task_snapshot = read_generation_cache(generation_task_key(row.public_id))
        worker_snapshot = read_generation_cache(generation_task_key("worker-db-1"))
        novel_snapshot = read_generation_cache(generation_novel_key(9901))

        assert task_snapshot is not None
        assert task_snapshot["status"] == "dispatching"
        assert worker_snapshot is not None
        assert worker_snapshot["task_id"] == row.public_id
        assert novel_snapshot is not None
        assert novel_snapshot["task_id"] == row.public_id
    finally:
        db.query(CreationTask).filter(CreationTask.resource_id == 9901).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_generation_cache_delete_does_not_break_db_rehydration_when_redis_unavailable(monkeypatch):
    monkeypatch.setattr(
        "app.services.generation.status_snapshot.get_generation_redis",
        lambda: _BrokenRedis(),
    )

    db = SessionLocal()
    try:
        row = CreationTask(
            user_uuid="test-admin-user",
            task_type="generation",
            resource_type="novel",
            resource_id=9902,
            status="running",
            phase="writer",
            message="写作章节草稿",
            worker_task_id="worker-db-2",
            payload_json={
                "novel_id": 9902,
                "novel_version_id": 1,
                "num_chapters": 12,
                "start_chapter": 1,
                "book_start_chapter": 1,
                "book_target_total_chapters": 12,
            },
        )
        db.add(row)
        db.commit()
        db.refresh(row)

        delete_generation_worker_cache("worker-db-2")
        delete_generation_novel_cache(9902)

        assert read_generation_cache(generation_task_key("worker-db-2"))["status"] == "running"
        assert read_generation_cache(generation_novel_key(9902))["status"] == "running"
    finally:
        db.query(CreationTask).filter(CreationTask.resource_id == 9902).delete(synchronize_session=False)
        db.commit()
        db.close()
