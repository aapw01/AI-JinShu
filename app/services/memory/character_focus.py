"""Build chapter-local character motivation and continuity context."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.novel import NovelMemory, StoryCharacterProfile


def _normalize_key(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    return re.sub(r"[^a-z0-9\u4e00-\u9fa5]+", "-", raw).strip("-")[:120]


def _text(value: Any, limit: int = 120) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = "、".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        parts = [f"{k}:{v}" for k, v in value.items() if v not in (None, "", [], {})]
        value = "；".join(parts)
    return str(value).strip()[:limit]


def _list(value: Any, *, limit: int = 6, item_limit: int = 80) -> list[str]:
    if value in (None, "", [], {}):
        return []
    values = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in values:
        text = _text(item, item_limit)
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _spec_characters(prewrite: dict[str, Any]) -> list[dict[str, Any]]:
    spec = (prewrite or {}).get("specification") or (prewrite or {}).get("spec") or {}
    chars = spec.get("characters") or spec.get("character_roster") or []
    return [dict(item) for item in chars if isinstance(item, dict) and _text(item.get("name"), 80)]


def _outline_text(outline: dict[str, Any]) -> str:
    fields = [
        "title",
        "outline",
        "role",
        "purpose",
        "chapter_objective",
        "conflict_axis",
        "relationship_delta",
        "opening_scene",
        "opening_character_positions",
        "required_irreversible_change",
    ]
    return "\n".join(_text((outline or {}).get(field), 500) for field in fields)


def _opening_position_for(name: str, outline: dict[str, Any]) -> str:
    positions = (outline or {}).get("opening_character_positions") or []
    if not isinstance(positions, list):
        positions = [positions]
    for item in positions:
        text = _text(item, 160)
        if name and name in text:
            return text
    return ""


def _opening_position_for_any(tokens: list[str], outline: dict[str, Any]) -> str:
    for token in tokens:
        found = _opening_position_for(token, outline)
        if found:
            return found
    return ""


def _character_tokens(spec: dict[str, Any]) -> list[str]:
    tokens = _list(spec.get("name"), limit=1, item_limit=80)
    for key in ("aliases", "alias", "nicknames", "nickname", "titles", "title"):
        tokens.extend(_list(spec.get(key), limit=8, item_limit=80))
    out: list[str] = []
    for token in tokens:
        if token and token not in out:
            out.append(token)
    return out


def _role_tokens(spec: dict[str, Any]) -> list[str]:
    role = _text(spec.get("role") or spec.get("type"), 80)
    if not role:
        return []
    tokens = [role]
    if "主角" in role or role.lower() in {"protagonist", "lead"}:
        tokens.extend(["主角", "男主", "女主", "主角团"])
    return [token for token in tokens if token]


def _state_by_key(character_states: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in character_states or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content") if isinstance(item.get("content"), dict) else item
        key = _normalize_key(str(item.get("key") or content.get("name") or ""))
        if key:
            out[key] = dict(content)
    return out


def _profile_by_key(profiles: list[StoryCharacterProfile]) -> dict[str, StoryCharacterProfile]:
    out: dict[str, StoryCharacterProfile] = {}
    for row in profiles or []:
        key = _normalize_key(str(getattr(row, "character_key", "") or getattr(row, "display_name", "") or ""))
        if key:
            out[key] = row
    return out


def _current_state(content: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "status": _text(content.get("status"), 60),
            "location": _text(content.get("location"), 80),
            "realm": _text(content.get("realm") or content.get("power_level") or content.get("cultivation"), 80),
            "emotional_state": _text(content.get("emotional_state") or content.get("mood") or content.get("emotion"), 80),
            "injuries": _list(content.get("injuries") or content.get("injury"), limit=4),
            "limitations": _list(content.get("limitations"), limit=4),
            "forbidden_actions": _list(content.get("forbidden_actions"), limit=4),
            "key_action": _text(content.get("key_action"), 120),
        }.items()
        if value not in ("", [], {})
    }


def _continuity_locks(content: dict[str, Any], profile: StoryCharacterProfile | None) -> list[str]:
    locks: list[str] = []
    locks.extend(_list(content.get("limitations"), limit=4))
    locks.extend(_list(content.get("forbidden_actions"), limit=4))
    locks.extend(_list(content.get("injuries") or content.get("injury"), limit=4))
    if profile is not None:
        locks.extend(_list(getattr(profile, "visual_do_not_change_json", None), limit=5))
        locks.extend(_list(getattr(profile, "signature_items_json", None), limit=4))
    deduped: list[str] = []
    for item in locks:
        if item and item not in deduped:
            deduped.append(item)
    return deduped[:10]


def _score_character(spec: dict[str, Any], outline: dict[str, Any], outline_blob: str, index: int) -> int:
    character_tokens = _character_tokens(spec)
    score = max(0, 40 - index)
    if any(token in outline_blob for token in character_tokens):
        score += 100
    if _opening_position_for_any(character_tokens, outline):
        score += 40
    role = _text(spec.get("role") or spec.get("type"), 80)
    if any(token in role.lower() for token in ("主角", "protagonist", "lead", "男主", "女主")):
        score += 20
    if any(token in outline_blob for token in _role_tokens(spec)):
        score += 60
    return score


def _is_outline_relevant(spec: dict[str, Any], outline: dict[str, Any], outline_blob: str) -> bool:
    character_tokens = _character_tokens(spec)
    if any(token in outline_blob for token in character_tokens):
        return True
    if _opening_position_for_any(character_tokens, outline):
        return True
    return any(token in outline_blob for token in _role_tokens(spec))


def build_character_focus_pack(
    *,
    prewrite: dict[str, Any],
    outline: dict[str, Any],
    character_states: list[dict[str, Any]] | None = None,
    profiles: list[StoryCharacterProfile] | None = None,
    max_items: int = 6,
) -> dict[str, Any]:
    """Build a compact per-chapter pack for character motivation and voice continuity."""
    specs = _spec_characters(prewrite)
    if not specs:
        return {"characters": [], "selected_character_keys": [], "selection_reason": "no_roster"}

    outline_blob = _outline_text(outline)
    states = _state_by_key(character_states or [])
    profile_map = _profile_by_key(profiles or [])
    indexed_specs = list(enumerate(specs))
    outline_relevant = [
        pair for pair in indexed_specs if _is_outline_relevant(pair[1], outline, outline_blob)
    ]
    candidate_specs = outline_relevant or indexed_specs
    ranked = sorted(
        candidate_specs,
        key=lambda pair: (-_score_character(pair[1], outline, outline_blob, pair[0]), pair[0]),
    )
    selected = [spec for _, spec in ranked[: max(1, int(max_items or 1))]]

    characters: list[dict[str, Any]] = []
    selected_keys: list[str] = []
    for spec in selected:
        name = _text(spec.get("name"), 80)
        key = _normalize_key(name)
        if not key:
            continue
        selected_keys.append(key)
        state = states.get(key, {})
        profile = profile_map.get(key)
        item = {
            "name": name,
            "role": _text(spec.get("role") or spec.get("type"), 80),
            "goal": _text(spec.get("goal") or spec.get("objective"), 120),
            "motivation": _text(spec.get("motivation") or spec.get("desire") or spec.get("why"), 160),
            "conflict": _text(spec.get("conflict") or spec.get("internal_conflict"), 160),
            "voice": _text(spec.get("voice") or spec.get("speech_style") or spec.get("dialogue_style"), 160),
            "personality": _text(spec.get("personality") or spec.get("traits"), 160),
            "opening_position": _opening_position_for_any(_character_tokens(spec), outline),
            "chapter_function": {
                key: value
                for key, value in {
                    "conflict_axis": _text((outline or {}).get("conflict_axis"), 180),
                    "relationship_delta": _text((outline or {}).get("relationship_delta"), 180),
                    "required_change": _text((outline or {}).get("required_irreversible_change"), 180),
                }.items()
                if value
            },
            "current_state": _current_state(state),
            "continuity_locks": _continuity_locks(state, profile),
        }
        characters.append({k: v for k, v in item.items() if v not in ("", [], {})})

    return {
        "characters": characters,
        "selected_character_keys": selected_keys,
        "selection_reason": "outline_mentions_then_roster_priority",
        "writer_rules": [
            "人物行动必须能回溯到 goal / motivation / current_state。",
            "对话要贴合 voice / personality，避免所有角色同一种口吻。",
            "continuity_locks 中的伤势、限制、标志物和禁止行为不得被无解释改写。",
        ],
    }


def load_character_focus_pack(
    db: Session,
    *,
    novel_id: int,
    novel_version_id: int | None,
    prewrite: dict[str, Any],
    outline: dict[str, Any],
    max_items: int = 6,
) -> dict[str, Any]:
    """Load character state/profile rows and build the chapter-local focus pack."""
    state_stmt = select(NovelMemory).where(
        NovelMemory.novel_id == int(novel_id),
        NovelMemory.memory_type == "character",
    )
    profile_stmt = select(StoryCharacterProfile).where(StoryCharacterProfile.novel_id == int(novel_id))
    if novel_version_id is not None:
        state_stmt = state_stmt.where(NovelMemory.novel_version_id == novel_version_id)
        profile_stmt = profile_stmt.where(StoryCharacterProfile.novel_version_id == novel_version_id)
    state_rows = db.execute(state_stmt).scalars().all()
    profile_rows = db.execute(profile_stmt).scalars().all()
    return build_character_focus_pack(
        prewrite=prewrite,
        outline=outline,
        character_states=[{"key": row.key, "content": row.content} for row in state_rows],
        profiles=list(profile_rows),
        max_items=max_items,
    )
