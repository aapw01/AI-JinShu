"""Replay bundle builder for chapter generation runtime snapshots."""
from __future__ import annotations

from typing import Any


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def build_chapter_replay_bundle(runtime_state: dict[str, Any] | None, *, chapter_num: int) -> dict[str, Any]:
    """Build a JSON-safe replay/debug bundle for one generated chapter."""
    chapter = int(chapter_num)
    state = _mapping(runtime_state)
    snapshots = _mapping(state.get("chapter_runtime_snapshots"))
    snapshot = _mapping(snapshots.get(str(chapter)) or snapshots.get(chapter))

    if not snapshot:
        return {
            "replay_status": "missing_snapshot",
            "chapter_num": chapter,
            "prompt": {},
            "context": {},
            "diagnostics": {},
        }

    return {
        "replay_status": "available",
        "chapter_num": int(snapshot.get("chapter_num") or chapter),
        "stage": snapshot.get("stage"),
        "prompt": _mapping(snapshot.get("prompt")),
        "context": _mapping(snapshot.get("context")),
        "diagnostics": _mapping(snapshot.get("diagnostics")),
    }
