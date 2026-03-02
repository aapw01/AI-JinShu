"""Trace ID context helpers."""
from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    return uuid4().hex


def set_trace_id(trace_id: object | None) -> None:
    if trace_id is None:
        _trace_id_var.set(None)
        return
    _trace_id_var.set(str(trace_id).strip() or None)


def get_trace_id() -> str | None:
    return _trace_id_var.get()
