"""Celery application."""
from celery import Celery
from app.core.config import get_settings

settings = get_settings()
app = Celery(
    "ai_jinshu",
    broker=settings.celery_broker_url,
    backend=settings.redis_url,
    include=["app.tasks.generation"],
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
)
