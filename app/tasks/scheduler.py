"""调度与恢复巡检的 Celery 定时任务。

这个模块很小，但面试价值很高，因为 `scheduler_tick` 和 `recovery_tick`
正好对应“正常派发”和“异常自愈”两条后台控制面。
"""
from __future__ import annotations

import logging

from app.core.database import SessionLocal
from app.services.scheduler.scheduler_service import dispatch_global, reclaim_stale_running_tasks, repair_active_dispatching_tasks
from app.workers.celery_app import app

logger = logging.getLogger(__name__)


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def scheduler_tick(self) -> dict[str, int]:
    """执行一次全局调度轮询，把排队任务派发给可用 Worker。"""
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
    """执行一次故障恢复巡检，修复卡死任务并重新触发调度。"""
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
