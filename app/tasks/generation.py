"""Generation Celery task - runs LangGraph pipeline, updates Redis and generation_tasks."""
import json
import logging
from app.workers.celery_app import app
from app.services.generation.pipeline import run_generation_pipeline

logger = logging.getLogger(__name__)


@app.task(bind=True)
def submit_generation_task(self, novel_id: str, num_chapters: int, start_chapter: int):
    """Run LangGraph pipeline for novel generation. Persists progress to generation_tasks."""
    from app.core.config import get_settings
    from app.core.database import SessionLocal
    from app.models.novel import GenerationTask, Novel
    import redis

    settings = get_settings()
    r = redis.from_url(settings.redis_url)
    task_id = self.request.id
    key = f"generation:{task_id}"
    novel_key = f"generation:novel:{novel_id}"

    # Reuse single DB session for the entire task
    db = SessionLocal()

    def progress_cb(step: str, chapter: int, pct: float, msg: str = "", meta: dict | None = None):
        meta = meta or {}
        data = {
            "status": meta.get("status", "running"),
            "step": step,
            "current_phase": meta.get("current_phase", step),
            "current_chapter": chapter,
            "total_chapters": meta.get("total_chapters", num_chapters),
            "progress": pct,
            "token_usage_input": meta.get("token_usage_input", 0),
            "token_usage_output": meta.get("token_usage_output", 0),
            "estimated_cost": meta.get("estimated_cost", 0.0),
            "message": msg,
        }
        r.setex(key, 86400, json.dumps(data))
        r.setex(novel_key, 86400, json.dumps(data))
        try:
            gt = db.query(GenerationTask).filter(GenerationTask.task_id == task_id).first()
            if gt:
                gt.status = data["status"]
                gt.step = step
                gt.current_phase = data["current_phase"]
                gt.current_chapter = chapter
                gt.total_chapters = data["total_chapters"]
                gt.progress = pct
                gt.message = msg
                gt.token_usage_input = data["token_usage_input"]
                gt.token_usage_output = data["token_usage_output"]
                gt.estimated_cost = data["estimated_cost"]
                if "final_report" in meta:
                    gt.final_report = meta["final_report"]
                db.commit()
        except Exception as e:
            logger.warning(f"Failed to update progress in DB: {e}")
            db.rollback()

    try:
        run_generation_pipeline(novel_id, num_chapters, start_chapter, progress_callback=progress_cb, task_id=task_id)
        data = {
            "status": "completed",
            "current_phase": "completed",
            "progress": 100,
            "current_chapter": start_chapter + num_chapters - 1,
            "total_chapters": num_chapters,
        }
        novel = db.query(Novel).filter(Novel.id == novel_id).first()
        if novel:
            novel.status = "completed"
            db.commit()
    except Exception as e:
        logger.error(f"Generation failed for novel {novel_id}: {e}")
        data = {"status": "failed", "current_phase": "failed", "progress": 0, "total_chapters": num_chapters, "error": str(e)}
        novel = db.query(Novel).filter(Novel.id == novel_id).first()
        if novel:
            novel.status = "failed"
            db.commit()

    # Final status update
    r.setex(key, 86400, json.dumps(data))
    r.setex(novel_key, 86400, json.dumps(data))
    try:
        gt = db.query(GenerationTask).filter(GenerationTask.task_id == task_id).first()
        if gt:
            gt.status = data["status"]
            gt.current_phase = data.get("current_phase")
            gt.progress = data.get("progress", 0)
            gt.current_chapter = data.get("current_chapter", 0)
            gt.total_chapters = data.get("total_chapters", num_chapters)
            gt.error = data.get("error")
            db.commit()
    except Exception as e:
        logger.error(f"Failed to update final status in DB: {e}")
        db.rollback()
    finally:
        db.close()

    return task_id
