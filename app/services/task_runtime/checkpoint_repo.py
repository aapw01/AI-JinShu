"""Checkpoint persistence helpers shared by async task handlers."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.creation_task import CreationTask, CreationTaskCheckpoint


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _resume_cursor_dict(row: CreationTask) -> dict[str, Any]:
    data = row.resume_cursor_json
    return dict(data) if isinstance(data, dict) else {}


def _merge_mapping(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in updates.items():
        if value is None:
            merged.pop(key, None)
            continue
        existing = merged.get(key)
        if isinstance(existing, dict) and isinstance(value, dict):
            merged[key] = _merge_mapping(existing, value)
        else:
            merged[key] = value
    return merged


def mark_unit_completed(
    db: Session,
    *,
    creation_task_id: int,
    unit_no: int,
    unit_type: str = "chapter",
    partition: str | None = None,
    payload: dict[str, Any] | None = None,
) -> CreationTaskCheckpoint:
    stmt = select(CreationTaskCheckpoint).where(
        CreationTaskCheckpoint.creation_task_id == creation_task_id,
        CreationTaskCheckpoint.unit_type == unit_type,
        CreationTaskCheckpoint.unit_no == int(unit_no),
        CreationTaskCheckpoint.partition == partition,
    )
    row = db.execute(stmt).scalar_one_or_none()
    if row:
        row.status = "completed"
        row.payload_json = payload or row.payload_json or {}
        row.updated_at = _utc_now()
        db.flush()
        return row
    try:
        with db.begin_nested():
            row = CreationTaskCheckpoint(
                creation_task_id=creation_task_id,
                unit_type=unit_type,
                unit_no=int(unit_no),
                partition=partition,
                status="completed",
                payload_json=payload or {},
            )
            db.add(row)
            db.flush()
        return row
    except IntegrityError:
        row = db.execute(stmt).scalar_one_or_none()
        if row is None:
            raise
        row.status = "completed"
        row.payload_json = payload or row.payload_json or {}
        row.updated_at = _utc_now()
        db.flush()
        return row


def get_last_completed_unit(
    db: Session,
    *,
    creation_task_id: int,
    unit_type: str = "chapter",
    partition: str | None = None,
    unit_from: int | None = None,
    unit_to: int | None = None,
) -> int | None:
    stmt = select(func.max(CreationTaskCheckpoint.unit_no)).where(
        CreationTaskCheckpoint.creation_task_id == creation_task_id,
        CreationTaskCheckpoint.unit_type == unit_type,
        CreationTaskCheckpoint.status == "completed",
    )
    if partition is None:
        stmt = stmt.where(CreationTaskCheckpoint.partition.is_(None))
    else:
        stmt = stmt.where(CreationTaskCheckpoint.partition == partition)
    if unit_from is not None:
        stmt = stmt.where(CreationTaskCheckpoint.unit_no >= int(unit_from))
    if unit_to is not None:
        stmt = stmt.where(CreationTaskCheckpoint.unit_no <= int(unit_to))
    value = db.execute(stmt).scalar_one_or_none()
    if value is None:
        return None
    return int(value)


def get_completed_units(
    db: Session,
    *,
    creation_task_id: int,
    unit_type: str = "chapter",
    partition: str | None = None,
) -> set[int]:
    stmt = select(CreationTaskCheckpoint.unit_no).where(
        CreationTaskCheckpoint.creation_task_id == creation_task_id,
        CreationTaskCheckpoint.unit_type == unit_type,
        CreationTaskCheckpoint.status == "completed",
    )
    if partition is None:
        stmt = stmt.where(CreationTaskCheckpoint.partition.is_(None))
    else:
        stmt = stmt.where(CreationTaskCheckpoint.partition == partition)
    return {int(x) for x in db.execute(stmt).scalars().all()}


def update_resume_cursor(
    db: Session,
    *,
    creation_task_id: int,
    next_unit_no: int,
    last_completed_unit_no: int | None,
    unit_type: str = "chapter",
    partition: str | None = None,
) -> None:
    row = db.execute(select(CreationTask).where(CreationTask.id == creation_task_id)).scalar_one_or_none()
    if not row:
        return
    payload = _resume_cursor_dict(row)
    payload.update({
        "unit_type": unit_type,
        "partition": partition,
        "last_completed": int(last_completed_unit_no) if last_completed_unit_no is not None else None,
        "next": int(next_unit_no),
    })
    row.resume_cursor_json = payload
    db.flush()


def get_resume_runtime_state(
    db: Session,
    *,
    creation_task_id: int,
) -> dict[str, Any]:
    row = db.execute(select(CreationTask).where(CreationTask.id == creation_task_id)).scalar_one_or_none()
    if not row:
        return {}
    payload = _resume_cursor_dict(row)
    runtime_state = payload.get("runtime_state")
    return dict(runtime_state) if isinstance(runtime_state, dict) else {}


def update_resume_runtime_state(
    db: Session,
    *,
    creation_task_id: int,
    runtime_state: dict[str, Any] | None,
) -> None:
    row = db.execute(select(CreationTask).where(CreationTask.id == creation_task_id)).scalar_one_or_none()
    if not row:
        return
    payload = _resume_cursor_dict(row)
    if runtime_state:
        existing_runtime = payload.get("runtime_state")
        existing_runtime = dict(existing_runtime) if isinstance(existing_runtime, dict) else {}
        payload["runtime_state"] = _merge_mapping(existing_runtime, dict(runtime_state))
    else:
        payload.pop("runtime_state", None)
    row.resume_cursor_json = payload
    db.flush()


def infer_next_unit(
    *,
    unit_from: int,
    unit_to: int,
    completed_units: Iterable[int],
) -> int:
    done = {int(x) for x in completed_units}
    start = int(unit_from)
    end = int(unit_to)
    for unit_no in range(start, end + 1):
        if unit_no not in done:
            return unit_no
    return end + 1
