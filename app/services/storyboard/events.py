"""Storyboard outbox + realtime event helpers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import redis
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.storyboard import StoryboardEventOutbox


_redis_pool = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _get_redis() -> redis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = redis.ConnectionPool.from_url(get_settings().redis_url)
    return redis.Redis(connection_pool=_redis_pool)


def append_event(
    db: Session,
    *,
    storyboard_project_id: int,
    storyboard_run_id: int | None,
    topic: str,
    event_key: str,
    payload: dict[str, Any] | None = None,
) -> StoryboardEventOutbox:
    row = StoryboardEventOutbox(
        storyboard_project_id=storyboard_project_id,
        storyboard_run_id=storyboard_run_id,
        topic=topic,
        event_key=event_key,
        payload_json=payload or {},
        status="pending",
        attempts=0,
    )
    db.add(row)
    db.flush()
    publish_event(row)
    db.flush()
    return row


def publish_event(row: StoryboardEventOutbox) -> None:
    row.attempts = int(row.attempts or 0) + 1
    try:
        payload = {
            "id": row.id,
            "topic": row.topic,
            "event_key": row.event_key,
            "storyboard_project_id": row.storyboard_project_id,
            "storyboard_run_id": row.storyboard_run_id,
            "payload": row.payload_json or {},
            "created_at": row.created_at.isoformat() if row.created_at else _utc_now().isoformat(),
        }
        _get_redis().publish("storyboard:events", json.dumps(payload, ensure_ascii=False))
        row.status = "published"
        row.published_at = _utc_now()
    except Exception:
        row.status = "failed"
    row.updated_at = _utc_now()
