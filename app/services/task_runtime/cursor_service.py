"""Cursor calculation helpers for resumable chapter workloads."""
from __future__ import annotations


def resume_from_last_completed(*, range_start: int, range_end: int, last_completed: int | None) -> int:
    start = int(range_start)
    end = int(range_end)
    if end < start:
        return start
    if last_completed is None:
        return start
    return max(start, int(last_completed) + 1)

