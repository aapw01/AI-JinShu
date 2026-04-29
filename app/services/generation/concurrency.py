"""Concurrency helpers for generation nodes."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Executor, Future
from contextvars import copy_context
from typing import TypeVar

T = TypeVar("T")


def submit_with_context(
    executor: Executor,
    fn: Callable[..., T],
    /,
    *args: object,
    **kwargs: object,
) -> Future[T]:
    """Submit work while preserving the caller's ContextVar state."""
    context = copy_context()
    return executor.submit(context.run, fn, *args, **kwargs)
