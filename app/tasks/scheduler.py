"""Background scheduler tick tasks."""
from __future__ import annotations

import logging

from app.core.database import SessionLocal
from app.services.scheduler.scheduler_service import dispatch_global, reclaim_stale_running_tasks, repair_active_dispatching_tasks
from app.workers.celery_app import app

logger = logging.getLogger(__name__)


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def scheduler_tick(self) -> dict[str, int]:
    db = SessionLocal()
    try:
        dispatched = dispatch_global(db)
        db.commit()
        return {"dispatched": int(dispatched)}
    except Exception:
        db.rollback()
        logger.exception("scheduler_tick failed")
        raise
    finally:
        db.close()


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def recovery_tick(self) -> dict[str, int]:
    db = SessionLocal()
    try:
        repaired = repair_active_dispatching_tasks(db)
        db.commit()
        reclaimed = reclaim_stale_running_tasks(db)
        db.commit()
        dispatched = dispatch_global(db)
        return {"repaired": int(repaired), "reclaimed": int(reclaimed), "dispatched": int(dispatched)}
    except Exception:
        db.rollback()
        logger.exception("recovery_tick failed")
        raise
    finally:
        db.close()
