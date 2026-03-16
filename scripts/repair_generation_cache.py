"""Rebuild generation Redis snapshots from CreationTask and clear stale worker keys."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.creation_task import CreationTask
from app.services.generation.status_snapshot import (
    build_generation_snapshot,
    delete_generation_worker_cache,
    sync_generation_novel_snapshot,
    write_generation_cache,
)


def main() -> None:
    db = SessionLocal()
    repaired_tasks = 0
    cleared_worker_keys = 0
    touched_novels: set[int] = set()
    statuses = defaultdict(int)
    try:
        tasks = list(
            db.execute(
                select(CreationTask)
                .where(
                    CreationTask.task_type == "generation",
                    CreationTask.resource_type == "novel",
                )
                .order_by(CreationTask.updated_at.desc(), CreationTask.id.desc())
            ).scalars().all()
        )
        for task in tasks:
            snapshot = build_generation_snapshot(task)
            write_generation_cache(
                task_public_id=task.public_id,
                novel_id=int(task.resource_id),
                payload=snapshot,
                worker_task_id=task.worker_task_id,
                mirror_worker=False,
                clear_worker_ids=[str(task.worker_task_id)] if task.worker_task_id else [],
                mirror_novel=False,
            )
            if task.worker_task_id:
                delete_generation_worker_cache(str(task.worker_task_id))
                cleared_worker_keys += 1
            repaired_tasks += 1
            touched_novels.add(int(task.resource_id))
            statuses[str(snapshot.get("status") or "unknown")] += 1

        for novel_id in touched_novels:
            sync_generation_novel_snapshot(db, novel_id=novel_id)
    finally:
        db.close()

    print(
        f"repaired_tasks={repaired_tasks} cleared_worker_keys={cleared_worker_keys} "
        f"touched_novels={len(touched_novels)} statuses={dict(sorted(statuses.items()))}"
    )


if __name__ == "__main__":
    main()
