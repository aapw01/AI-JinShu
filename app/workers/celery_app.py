"""Celery application."""
from celery import Celery
from app.core.config import get_settings
from app.core.logging_config import setup_logging

settings = get_settings()
setup_logging()
app = Celery(
    "ai_jinshu",
    broker=settings.celery_broker_url,
    backend=settings.redis_url,
    include=["app.tasks.generation", "app.tasks.rewrite", "app.tasks.storyboard", "app.tasks.scheduler"],
)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # Reliability: acknowledge only after task completes, so worker restarts
    # will cause in-flight tasks to be re-delivered.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Better fairness for long-running generation tasks.
    worker_prefetch_multiplier=1,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 3600},
    beat_schedule={
        "creation-scheduler-tick": {
            "task": "app.tasks.scheduler.scheduler_tick",
            "schedule": max(1, int(settings.creation_dispatch_poll_seconds or 2)),
        },
        "creation-recovery-tick": {
            "task": "app.tasks.scheduler.recovery_tick",
            "schedule": max(1, int(settings.creation_recovery_poll_seconds or 5)),
        },
    },
)
