"""Segment-plan helpers for stable retry/resume behavior."""
from __future__ import annotations

from typing import Any

from app.services.generation.common import normalize_outline_payload, upsert_chapter_outline

_VALID_PLAN_KINDS = {"normal", "volume_replan", "bridge", "tail_rewrite"}


def merge_outlines(
    existing: list[dict[str, Any]] | None,
    additions: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for source in (existing or []):
        if not isinstance(source, dict):
            continue
        chapter_num = int(source.get("chapter_num") or 0)
        if chapter_num <= 0:
            continue
        merged[chapter_num] = dict(source)
    for source in (additions or []):
        if not isinstance(source, dict):
            continue
        chapter_num = int(source.get("chapter_num") or 0)
        if chapter_num <= 0:
            continue
        merged[chapter_num] = normalize_outline_payload(chapter_num, source)
    return [merged[key] for key in sorted(merged)]


def build_segment_plan(
    *,
    start_chapter: int,
    end_chapter: int,
    volume_no: int,
    outlines: list[dict[str, Any]] | None,
    plan_kind: str = "normal",
) -> dict[str, Any]:
    start = int(start_chapter)
    end = int(end_chapter)
    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for source in (outlines or []):
        if not isinstance(source, dict):
            continue
        chapter_num = int(source.get("chapter_num") or 0)
        if chapter_num < start or chapter_num > end or chapter_num in seen:
            continue
        normalized.append(normalize_outline_payload(chapter_num, source))
        seen.add(chapter_num)
    normalized.sort(key=lambda item: int(item.get("chapter_num") or 0))
    kind = str(plan_kind or "normal")
    if kind not in _VALID_PLAN_KINDS:
        kind = "normal"
    return {
        "start_chapter": start,
        "end_chapter": end,
        "volume_no": max(1, int(volume_no or 1)),
        "plan_kind": kind,
        "outlines": normalized,
    }


def segment_plan_covers_range(
    segment_plan: dict[str, Any] | None,
    *,
    start_chapter: int,
    end_chapter: int,
) -> bool:
    if not isinstance(segment_plan, dict):
        return False
    start = int(start_chapter)
    end = int(end_chapter)
    if int(segment_plan.get("start_chapter") or 0) != start:
        return False
    if int(segment_plan.get("end_chapter") or 0) != end:
        return False
    required = set(range(start, end + 1))
    available = {
        int(item.get("chapter_num") or 0)
        for item in (segment_plan.get("outlines") or [])
        if isinstance(item, dict) and int(item.get("chapter_num") or 0) > 0
    }
    return required.issubset(available)


def restore_segment_plan_outlines(
    *,
    novel_id: int,
    novel_version_id: int | None,
    segment_plan: dict[str, Any],
    db=None,
) -> list[dict[str, Any]]:
    restored: list[dict[str, Any]] = []
    for item in (segment_plan.get("outlines") or []):
        if not isinstance(item, dict):
            continue
        restored.append(
            upsert_chapter_outline(
                novel_id,
                item,
                novel_version_id=novel_version_id,
                db=db,
            )
        )
    return restored
