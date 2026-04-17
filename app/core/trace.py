"""Trace ID context helpers."""
from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    """生成新的 trace_id，供请求链路和异步任务串联日志。"""
    return uuid4().hex


def set_trace_id(trace_id: object | None) -> None:
    """把 trace_id 写入当前上下文，方便后续日志自动继承。"""
    if trace_id is None:
        _trace_id_var.set(None)
        return
    _trace_id_var.set(str(trace_id).strip() or None)


def get_trace_id() -> str | None:
    """读取当前上下文中的 trace_id。"""
    return _trace_id_var.get()
