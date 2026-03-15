from datetime import datetime, timedelta, timezone
import sys
import types

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.creation_task import CreationTask
from app.models.novel import Novel, NovelVersion
from app.models.storyboard import StoryboardProject, StoryboardTask, StoryboardVersion
from app.tasks.generation import _resolve_generation_resume
from app.services.scheduler.scheduler_service import dispatch_user_queue, reclaim_stale_running_tasks
from app.services.task_runtime.checkpoint_repo import get_last_completed_unit, mark_unit_completed
from app.services.task_runtime.cursor_service import resume_from_last_completed


def _seed_creation_task(*, start_chapter: int = 1, num_chapters: int = 5) -> int:
    db = SessionLocal()
    try:
        row = CreationTask(
            user_uuid="u-runtime",
            task_type="generation",
            resource_type="novel",
            resource_id=1,
            status="running",
            payload_json={"novel_id": 1, "start_chapter": start_chapter, "num_chapters": num_chapters},
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


def test_resume_plan_prefers_tail_rewrite_runtime_state():
    task_id = _seed_creation_task(start_chapter=1, num_chapters=15)
    db = SessionLocal()
    try:
        row = db.execute(select(CreationTask).where(CreationTask.id == task_id)).scalar_one()
        row.resume_cursor_json = {
            "unit_type": "chapter",
            "partition": None,
            "last_completed": 15,
            "next": 16,
            "runtime_state": {
                "node": "tail_rewrite",
                "resume_from_chapter": 13,
                "effective_end_chapter": 15,
                "effective_total_chapters": 15,
                "tail_rewrite_attempts": 1,
                "bridge_attempts": 0,
                "terminal": False,
            },
        }
        db.commit()
    finally:
        db.close()

    plan = _resolve_generation_resume(task_id, start_chapter=1, num_chapters=15)
    assert plan.mode == "chapter_range"
    assert plan.start_chapter == 13
    assert plan.num_chapters == 3
    assert plan.display_total_chapters == 15


def test_resume_plan_prefers_bridge_runtime_state():
    task_id = _seed_creation_task(start_chapter=1, num_chapters=15)
    db = SessionLocal()
    try:
        row = db.execute(select(CreationTask).where(CreationTask.id == task_id)).scalar_one()
        row.resume_cursor_json = {
            "unit_type": "chapter",
            "partition": None,
            "last_completed": 15,
            "next": 16,
            "runtime_state": {
                "node": "bridge_chapter",
                "resume_from_chapter": 16,
                "effective_end_chapter": 16,
                "effective_total_chapters": 16,
                "tail_rewrite_attempts": 2,
                "bridge_attempts": 1,
                "terminal": False,
            },
        }
        db.commit()
    finally:
        db.close()

    plan = _resolve_generation_resume(task_id, start_chapter=1, num_chapters=15)
    assert plan.mode == "chapter_range"
    assert plan.start_chapter == 16
    assert plan.num_chapters == 1
    assert plan.display_total_chapters == 16


def test_resume_plan_prefers_final_book_review_runtime_state():
    task_id = _seed_creation_task(start_chapter=1, num_chapters=15)
    db = SessionLocal()
    try:
        row = db.execute(select(CreationTask).where(CreationTask.id == task_id)).scalar_one()
        row.resume_cursor_json = {
            "unit_type": "chapter",
            "partition": None,
            "last_completed": 15,
            "next": 16,
            "runtime_state": {
                "node": "final_book_review",
                "resume_from_chapter": 16,
                "effective_end_chapter": 15,
                "effective_total_chapters": 15,
                "tail_rewrite_attempts": 2,
                "bridge_attempts": 0,
                "terminal": False,
            },
        }
        db.commit()
    finally:
        db.close()

    plan = _resolve_generation_resume(task_id, start_chapter=1, num_chapters=15)
    assert plan.mode == "final_book_review"
    assert plan.start_chapter == 1
    assert plan.num_chapters == 15
    assert plan.display_total_chapters == 15


def test_resume_plan_tail_rewrite_with_offset_range_keeps_absolute_total():
    task_id = _seed_creation_task(start_chapter=18, num_chapters=3)
    db = SessionLocal()
    try:
        row = db.execute(select(CreationTask).where(CreationTask.id == task_id)).scalar_one()
        row.payload_json = {
            "novel_id": 1,
            "start_chapter": 18,
            "num_chapters": 3,
            "original_total_chapters": 20,
        }
        row.resume_cursor_json = {
            "unit_type": "chapter",
            "partition": None,
            "last_completed": 19,
            "next": 20,
            "runtime_state": {
                "node": "tail_rewrite",
                "resume_from_chapter": 19,
                "effective_end_chapter": 20,
                "effective_total_chapters": 20,
                "tail_rewrite_attempts": 1,
                "bridge_attempts": 0,
                "terminal": False,
            },
        }
        db.commit()
    finally:
        db.close()

    plan = _resolve_generation_resume(task_id, start_chapter=18, num_chapters=3)
    assert plan.mode == "chapter_range"
    assert plan.start_chapter == 19
    assert plan.num_chapters == 2
    assert plan.display_total_chapters == 20


def test_resume_plan_final_review_with_offset_range_reconstructs_full_span():
    task_id = _seed_creation_task(start_chapter=18, num_chapters=3)
    db = SessionLocal()
    try:
        row = db.execute(select(CreationTask).where(CreationTask.id == task_id)).scalar_one()
        row.payload_json = {
            "novel_id": 1,
            "start_chapter": 18,
            "num_chapters": 3,
            "original_total_chapters": 20,
        }
        row.resume_cursor_json = {
            "unit_type": "chapter",
            "partition": None,
            "last_completed": 20,
            "next": 21,
            "runtime_state": {
                "node": "final_book_review",
                "resume_from_chapter": 21,
                "effective_end_chapter": 20,
                "effective_total_chapters": 20,
                "tail_rewrite_attempts": 2,
                "bridge_attempts": 0,
                "terminal": False,
            },
        }
        db.commit()
    finally:
        db.close()

    plan = _resolve_generation_resume(task_id, start_chapter=18, num_chapters=3)
    assert plan.mode == "final_book_review"
    assert plan.start_chapter == 1
    assert plan.num_chapters == 20
    assert plan.display_total_chapters == 20


def test_reclaim_stale_running_task(monkeypatch):
    monkeypatch.setattr(
        "app.services.scheduler.scheduler_service.dispatch_user_queue",
        lambda db, *, user_uuid: [],
    )
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

    assert reclaimed >= 1
    assert row.status == "queued"
    assert row.worker_task_id is None
    assert int(row.recovery_count or 0) >= 1


def test_dispatch_storyboard_novel_version_mismatch_fails_before_worker_enqueue(monkeypatch):
    called = {"count": 0}

    class _DummyAsyncResult:
        id = "storyboard-worker-task-id"

    class _DummyStoryboardTask:
        @staticmethod
        def delay(**kwargs):  # pragma: no cover - should not be called in this test
            called["count"] += 1
            return _DummyAsyncResult()

    monkeypatch.setitem(
        sys.modules,
        "app.tasks.storyboard",
        types.SimpleNamespace(run_storyboard_pipeline=_DummyStoryboardTask),
    )

    db = SessionLocal()
    try:
        novel_a = Novel(title="dispatch-source-a", target_language="zh", user_id="u-storyboard-version-mismatch", status="completed")
        novel_b = Novel(title="dispatch-source-b", target_language="zh", user_id="u-storyboard-version-mismatch", status="completed")
        db.add_all([novel_a, novel_b])
        db.flush()

        version_b = NovelVersion(novel_id=int(novel_b.id), version_no=1, status="completed", is_default=1)
        db.add(version_b)
        db.flush()

        project = StoryboardProject(
            novel_id=int(novel_a.id),
            owner_user_uuid="u-storyboard-version-mismatch",
            status="draft",
            target_episodes=2,
            target_episode_seconds=90,
            output_lanes=["vertical_feed"],
            active_lane="vertical_feed",
        )
        db.add(project)
        db.flush()

        version = StoryboardVersion(
            storyboard_project_id=int(project.id),
            source_novel_version_id=int(version_b.id),
            version_no=1,
            lane="vertical_feed",
            status="draft",
            is_default=1,
            is_final=0,
        )
        db.add(version)
        db.flush()

        task_db = StoryboardTask(
            storyboard_project_id=int(project.id),
            task_id="pending-dispatch-mismatch",
            status="submitted",
            run_state="submitted",
        )
        db.add(task_db)
        db.flush()

        row = CreationTask(
            user_uuid="u-storyboard-version-mismatch",
            task_type="storyboard",
            resource_type="storyboard_project",
            resource_id=int(project.id),
            status="queued",
            payload_json={
                "project_id": int(project.id),
                "task_db_id": int(task_db.id),
                "novel_version_id": int(version_b.id),
                "version_ids": [int(version.id)],
            },
        )
        db.add(row)
        db.commit()
        db.refresh(row)

        dispatched = dispatch_user_queue(db, user_uuid=row.user_uuid)
        db.commit()
        db.refresh(row)
    finally:
        db.close()

    assert dispatched == []
    assert row.status == "failed"
    assert row.error_code == "DISPATCH_PAYLOAD_INVALID"
    assert "storyboard novel_version context invalid" in str(row.error_detail or "")
    assert called["count"] == 0
