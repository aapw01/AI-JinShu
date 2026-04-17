"""Promote active dispatching CreationTasks that are already heartbeating to running."""
from __future__ import annotations

from app.core.database import SessionLocal
from app.services.scheduler.scheduler_service import repair_active_dispatching_tasks


def main() -> None:
    """执行一次 dispatching 任务修复，并打印修复条数。"""
    db = SessionLocal()
    try:
        repaired = repair_active_dispatching_tasks(db)
        db.commit()
    finally:
        db.close()
    print(f"repaired_active_creation_tasks={repaired}")


if __name__ == "__main__":
    main()
