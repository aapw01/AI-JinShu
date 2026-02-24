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
)
