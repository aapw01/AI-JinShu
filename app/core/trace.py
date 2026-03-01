"""Trace ID context helpers."""
from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

_trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)


def new_trace_id() -> str:
    return uuid4().hex


def set_trace_id(trace_id: str | None) -> None:
    _trace_id_var.set((trace_id or "").strip() or None)


def get_trace_id() -> str | None:
    return _trace_id_var.get()

