"""统一任务运行时的 checkpoint / resume cursor 持久化辅助。

模块职责：
- 记录章节等执行单元的完成边界。
- 维护 `resume_cursor_json`，告诉恢复逻辑“下次从哪继续”。
- 持久化恢复所需的轻量 runtime_state。

系统位置：
- 上游是 generation / rewrite / storyboard 等长任务执行器。
- 下游是恢复逻辑 `resume_from_last_completed()` 与任务重派流程。

面试可讲点：
- 为什么 checkpoint 只存“完成边界”，而不是把 worker 内存直接持久化。
- 为什么 `runtime_state` 和 `last_completed/next` 要分开存。
- 为什么要在并发场景下保证 `mark_unit_completed()` 幂等。
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.creation_task import CreationTask, CreationTaskCheckpoint


def _utc_now() -> datetime:
    """返回当前 UTC 时间，统一任务与数据库时间基准。"""
    return datetime.now(timezone.utc)


def _resume_cursor_dict(row: CreationTask) -> dict[str, Any]:
    """安全读取任务上的恢复游标字典，缺失时返回空 dict。"""
    data = row.resume_cursor_json
    return dict(data) if isinstance(data, dict) else {}


def _merge_mapping(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """递归合并运行时状态片段。

    恢复状态会由多个节点逐步补齐，直接整块覆盖容易丢字段，
    因此这里采用“仅覆盖显式更新字段”的 merge 语义。
    """
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
    """把指定执行单元标记为已完成，并在并发场景下保持幂等。"""
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
    """返回指定范围内最后一个已完成的执行单元编号。"""
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
    """返回指定任务已完成的所有执行单元编号。"""
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
    """更新任务恢复游标，记录 `last_completed` 与 `next`。

    这是最轻量、最关键的恢复锚点。面试里可以直接说：
    “系统不恢复 worker 内存现场，只恢复到最近稳定完成的章节边界。”
    """
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
    """读取任务恢复所需的运行时状态片段。"""
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
    """合并写入恢复所需的运行时状态片段。

    这里存的是当前分卷、segment 边界、特殊模式等“业务上下文”，
    不等同于 checkpoint。checkpoint 负责“做到哪”，runtime_state 负责
    “以什么模式继续”。
    """
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
    """根据已完成集合推断下一个尚未执行的单元编号。"""
    done = {int(x) for x in completed_units}
    start = int(unit_from)
    end = int(unit_to)
    for unit_no in range(start, end + 1):
        if unit_no not in done:
            return unit_no
    return end + 1
