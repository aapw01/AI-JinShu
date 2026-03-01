from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.creation_task import CreationTask
from app.services.scheduler.scheduler_service import reclaim_stale_running_tasks
from app.services.task_runtime.checkpoint_repo import get_last_completed_unit, mark_unit_completed
from app.services.task_runtime.cursor_service import resume_from_last_completed


def _seed_creation_task() -> int:
    db = SessionLocal()
    try:
        row = CreationTask(
            user_uuid="u-runtime",
            task_type="generation",
            resource_type="novel",
            resource_id=1,
            status="running",
            payload_json={"novel_id": 1, "start_chapter": 1, "num_chapters": 5},
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return int(row.id)
    finally:
        db.close()


def test_resume_boundary_requires_completed_checkpoint():
    task_id = _seed_creation_task()
    db = SessionLocal()
    try:
        mark_unit_completed(db, creation_task_id=task_id, unit_type="chapter", unit_no=1)
        mark_unit_completed(db, creation_task_id=task_id, unit_type="chapter", unit_no=2)
        db.commit()
        last_completed = get_last_completed_unit(
            db,
            creation_task_id=task_id,
            unit_type="chapter",
            unit_from=1,
            unit_to=5,
        )
    finally:
        db.close()
    # chapter 3 not completed => resume must restart from 3
    assert resume_from_last_completed(range_start=1, range_end=5, last_completed=last_completed) == 3


def test_resume_boundary_advances_when_checkpoint_completed():
    task_id = _seed_creation_task()
    db = SessionLocal()
    try:
        for n in (1, 2, 3):
            mark_unit_completed(db, creation_task_id=task_id, unit_type="chapter", unit_no=n)
        db.commit()
        last_completed = get_last_completed_unit(
            db,
            creation_task_id=task_id,
            unit_type="chapter",
            unit_from=1,
            unit_to=5,
        )
    finally:
        db.close()
    assert resume_from_last_completed(range_start=1, range_end=5, last_completed=last_completed) == 4


def test_reclaim_stale_running_task():
    task_id = _seed_creation_task()
    db = SessionLocal()
    try:
        row = db.execute(select(CreationTask).where(CreationTask.id == task_id)).scalar_one()
        row.status = "running"
        row.phase = "chapter_writing"
        row.worker_task_id = "worker-1"
        row.worker_lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        reclaimed = reclaim_stale_running_tasks(db)
        db.commit()
        row = db.execute(select(CreationTask).where(CreationTask.id == task_id)).scalar_one()
    finally:
        db.close()

    assert reclaimed == 1
    assert row.status == "queued"
    assert row.worker_task_id is None
    assert int(row.recovery_count or 0) >= 1
