"""Structured progression memory and outline contract helpers."""
from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Any, Optional

from sqlalchemy import Integer, cast, desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.novel import NovelMemory, NovelMemoryRevision

RECENT_ADVANCEMENT_LIMIT = 5

logger = logging.getLogger(__name__)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw = value
    else:
        raw = [value]
    seen: list[str] = []
    for item in raw:
        text = _clean_text(item)
        if text and text not in seen:
            seen.append(text)
    return seen


def _best_similarity(left: str, candidates: list[str]) -> float:
    src = _clean_text(left)
    if not src:
        return 0.0
    best = 0.0
    for candidate in candidates:
        target = _clean_text(candidate)
        if not target:
            continue
        if src == target or src in target or target in src:
            return 1.0
        best = max(best, SequenceMatcher(a=src, b=target).ratio())
    return best


def _chapter_num_from_key(key: Any) -> int:
    try:
        return int(str(key or "0").strip())
    except Exception:
        return 0


def _classify_payoff_kind(text: str) -> str:
    raw = _clean_text(text)
    if not raw:
        return ""
    mapping = {
        "truth_reveal": ["真相", "揭秘", "身份", "秘密", "答案"],
        "relationship_shift": ["和解", "告白", "婚约", "吃醋", "认亲", "翻脸"],
        "power_display": ["打脸", "反杀", "镇压", "爆发", "逆袭"],
        "rescue": ["救", "解围", "保护", "营救"],
        "revenge": ["复仇", "报复", "清算"],
        "investigation": ["调查", "线索", "证据", "追查"],
        "occult_reveal": ["封印", "祭坛", "邪", "诡", "阵法"],
    }
    for key, keywords in mapping.items():
        if any(keyword in raw for keyword in keywords):
            return key
    return "general_payoff"


def _classify_reveal_kind(text: str) -> str:
    raw = _clean_text(text)
    if not raw:
        return ""
    mapping = {
        "identity": ["身份", "身世", "血脉", "真千金", "认亲"],
        "relationship": ["婚约", "未婚妻", "喜欢", "爱", "和解"],
        "truth": ["真相", "秘密", "内幕", "幕后"],
        "supernatural": ["封印", "邪祟", "阵法", "祭", "归墟", "宿命"],
        "investigation": ["线索", "证据", "调查", "追查"],
    }
    for key, keywords in mapping.items():
        if any(keyword in raw for keyword in keywords):
            return key
    return "general_reveal"


def normalize_outline_contract(outline: dict[str, Any] | None, chapter_num: int) -> dict[str, Any]:
    data = dict(outline or {})
    purpose = _clean_text(data.get("chapter_objective") or data.get("purpose") or data.get("outline"))
    payoff = _clean_text(data.get("payoff"))
    hook = _clean_text(data.get("hook"))
    summary = _clean_text(data.get("summary"))
    role = _clean_text(data.get("role"))
    transition_mode = _clean_text(data.get("transition_mode")) or "direct"
    opening_scene = _clean_text(data.get("opening_scene"))
    if not opening_scene:
        opening_scene = _clean_text(data.get("title")) or f"第{chapter_num}章开场"
    required_new_information = _string_list(
        data.get("required_new_information")
        or ([payoff] if payoff else [])
        or ([hook] if hook else [])
    )
    required_irreversible_change = _clean_text(
        data.get("required_irreversible_change")
        or data.get("mini_climax")
        or payoff
    )
    relationship_delta = _clean_text(data.get("relationship_delta"))
    conflict_axis = _clean_text(data.get("conflict_axis") or role or purpose)
    forbidden_repeats = _string_list(data.get("forbidden_repeats"))
    if not forbidden_repeats:
        for item in [purpose, payoff, hook]:
            if item:
                forbidden_repeats.append(item[:80])
    contract = {
        "chapter_objective": purpose or f"推进第{chapter_num}章主线",
        "required_new_information": required_new_information[:4],
        "required_irreversible_change": required_irreversible_change[:160],
        "relationship_delta": relationship_delta[:160],
        "conflict_axis": conflict_axis[:160],
        "payoff_kind": _clean_text(data.get("payoff_kind")) or _classify_payoff_kind(payoff),
        "reveal_kind": _clean_text(data.get("reveal_kind"))
        or _classify_reveal_kind(" ".join(required_new_information) or summary or payoff or hook),
        "forbidden_repeats": forbidden_repeats[:6],
        "opening_scene": opening_scene[:160],
        "opening_character_positions": _string_list(data.get("opening_character_positions"))[:6],
        "opening_time_state": _clean_text(data.get("opening_time_state"))[:120],
        "transition_mode": transition_mode[:80],
    }
    return {**data, **contract}


class ProgressionMemoryManager:
    """NovelMemory-backed progression and continuity state."""

    def _record_revision(
        self,
        *,
        novel_id: int,
        novel_version_id: int | None,
        memory_type: str,
        memory_key: str,
        action: str,
        old_content: dict[str, Any] | None,
        new_content: dict[str, Any] | None,
        source_chapter_num: int | None = None,
        promotion_score: float | None = None,
        db: Session,
    ) -> None:
        db.add(
            NovelMemoryRevision(
                novel_id=novel_id,
                novel_version_id=novel_version_id,
                memory_type=memory_type,
                memory_key=memory_key,
                source_chapter_num=source_chapter_num,
                action=str(action or "upsert")[:32],
                old_content=dict(old_content or {}),
                new_content=dict(new_content or {}),
                promotion_score=float(promotion_score) if promotion_score is not None else None,
            )
        )

    def _get_memory(
        self,
        novel_id: int,
        memory_type: str,
        key: str,
        *,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> dict[str, Any] | None:
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(NovelMemory).where(
                NovelMemory.novel_id == novel_id,
                NovelMemory.memory_type == memory_type,
                NovelMemory.key == key,
            )
            if novel_version_id is not None:
                stmt = stmt.where(NovelMemory.novel_version_id == novel_version_id)
            row = db.execute(stmt).scalar_one_or_none()
            return dict(row.content or {}) if row and isinstance(row.content, dict) else None
        finally:
            if should_close:
                db.close()

    def _upsert_memory(
        self,
        novel_id: int,
        memory_type: str,
        key: str,
        content: dict[str, Any],
        *,
        novel_version_id: int | None = None,
        action: str = "upsert",
        source_chapter_num: int | None = None,
        promotion_score: float | None = None,
        db: Optional[Session] = None,
    ) -> None:
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(NovelMemory).where(
                NovelMemory.novel_id == novel_id,
                NovelMemory.memory_type == memory_type,
                NovelMemory.key == key,
            )
            if novel_version_id is not None:
                stmt = stmt.where(NovelMemory.novel_version_id == novel_version_id)
            existing = db.execute(stmt).scalar_one_or_none()
            old_content = dict(existing.content or {}) if existing and isinstance(existing.content, dict) else {}
            if existing:
                existing.content = content
            else:
                try:
                    with db.begin_nested():
                        db.add(
                            NovelMemory(
                                novel_id=novel_id,
                                novel_version_id=novel_version_id,
                                memory_type=memory_type,
                                key=key,
                                content=content,
                            )
                        )
                        db.flush()
                except IntegrityError:
                    try:
                        existing = db.execute(stmt).scalar_one_or_none()
                    except Exception:
                        logger.warning(
                            "progression memory upsert fallback lookup failed novel_id=%s memory_type=%s key=%s",
                            novel_id,
                            memory_type,
                            key,
                            exc_info=True,
                        )
                        db.rollback()
                        raise
                    if existing:
                        existing.content = content
                    else:
                        raise
            db.flush()
            if old_content != dict(content or {}):
                self._record_revision(
                    novel_id=novel_id,
                    novel_version_id=novel_version_id,
                    memory_type=memory_type,
                    memory_key=key,
                    action=action,
                    old_content=old_content,
                    new_content=dict(content or {}),
                    source_chapter_num=source_chapter_num,
                    promotion_score=promotion_score,
                    db=db,
                )
                db.flush()
            if should_close:
                db.commit()
        except Exception:
            if should_close:
                db.rollback()
            raise
        finally:
            if should_close:
                db.close()

    def delete_memory(
        self,
        novel_id: int,
        memory_type: str,
        key: str,
        *,
        novel_version_id: int | None = None,
        action: str = "delete",
        source_chapter_num: int | None = None,
        promotion_score: float | None = None,
        db: Optional[Session] = None,
    ) -> bool:
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(NovelMemory).where(
                NovelMemory.novel_id == novel_id,
                NovelMemory.memory_type == memory_type,
                NovelMemory.key == key,
            )
            if novel_version_id is not None:
                stmt = stmt.where(NovelMemory.novel_version_id == novel_version_id)
            existing = db.execute(stmt).scalar_one_or_none()
            if existing is None:
                if should_close:
                    db.commit()
                return False
            old_content = dict(existing.content or {}) if isinstance(existing.content, dict) else {}
            db.delete(existing)
            db.flush()
            self._record_revision(
                novel_id=novel_id,
                novel_version_id=novel_version_id,
                memory_type=memory_type,
                memory_key=key,
                action=action,
                old_content=old_content,
                new_content={},
                source_chapter_num=source_chapter_num,
                promotion_score=promotion_score,
                db=db,
            )
            db.flush()
            if should_close:
                db.commit()
            return True
        except Exception:
            if should_close:
                db.rollback()
            raise
        finally:
            if should_close:
                db.close()

    def delete_memories_from_chapter(
        self,
        novel_id: int,
        memory_type: str,
        from_chapter: int,
        *,
        novel_version_id: int | None = None,
        action: str = "rollback",
        promotion_score: float | None = None,
        db: Optional[Session] = None,
    ) -> int:
        should_close = db is None
        db = db or SessionLocal()
        try:
            chapter_key = cast(NovelMemory.key, Integer)
            stmt = select(NovelMemory).where(
                NovelMemory.novel_id == novel_id,
                NovelMemory.memory_type == memory_type,
                chapter_key >= int(from_chapter),
            )
            if novel_version_id is not None:
                stmt = stmt.where(NovelMemory.novel_version_id == novel_version_id)
            rows = db.execute(stmt).scalars().all()
            deleted = 0
            for row in rows:
                old_content = dict(row.content or {}) if isinstance(row.content, dict) else {}
                key = str(row.key or "")
                db.delete(row)
                deleted += 1
                self._record_revision(
                    novel_id=novel_id,
                    novel_version_id=novel_version_id,
                    memory_type=memory_type,
                    memory_key=key,
                    action=action,
                    old_content=old_content,
                    new_content={},
                    source_chapter_num=_chapter_num_from_key(key) or int(from_chapter),
                    promotion_score=promotion_score,
                    db=db,
                )
            db.flush()
            if should_close:
                db.commit()
            return deleted
        except Exception:
            if should_close:
                db.rollback()
            raise
        finally:
            if should_close:
                db.close()

    def delete_memories_by_type(
        self,
        novel_id: int,
        memory_type: str,
        *,
        novel_version_id: int | None = None,
        action: str = "delete",
        source_chapter_num: int | None = None,
        promotion_score: float | None = None,
        db: Optional[Session] = None,
    ) -> int:
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(NovelMemory).where(
                NovelMemory.novel_id == novel_id,
                NovelMemory.memory_type == memory_type,
            )
            if novel_version_id is not None:
                stmt = stmt.where(NovelMemory.novel_version_id == novel_version_id)
            rows = db.execute(stmt).scalars().all()
            deleted = 0
            for row in rows:
                old_content = dict(row.content or {}) if isinstance(row.content, dict) else {}
                key = str(row.key or "")
                db.delete(row)
                deleted += 1
                self._record_revision(
                    novel_id=novel_id,
                    novel_version_id=novel_version_id,
                    memory_type=memory_type,
                    memory_key=key,
                    action=action,
                    old_content=old_content,
                    new_content={},
                    source_chapter_num=source_chapter_num,
                    promotion_score=promotion_score,
                    db=db,
                )
            db.flush()
            if should_close:
                db.commit()
            return deleted
        except Exception:
            if should_close:
                db.rollback()
            raise
        finally:
            if should_close:
                db.close()

    def list_recent_advancements(
        self,
        novel_id: int,
        before_chapter: int,
        *,
        limit: int = RECENT_ADVANCEMENT_LIMIT,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> list[dict[str, Any]]:
        should_close = db is None
        db = db or SessionLocal()
        try:
            fetch_limit = max(limit * 3, limit + 5)
            chapter_key = cast(NovelMemory.key, Integer)
            stmt = select(NovelMemory).where(
                NovelMemory.novel_id == novel_id,
                NovelMemory.memory_type == "chapter_advancement",
                chapter_key > 0,
                chapter_key < before_chapter,
            )
            if novel_version_id is not None:
                stmt = stmt.where(NovelMemory.novel_version_id == novel_version_id)
            stmt = stmt.order_by(desc(chapter_key), desc(NovelMemory.id)).limit(fetch_limit)
            rows = db.execute(stmt).scalars().all()
            result: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row.content, dict):
                    continue
                chapter_num = int(row.content.get("chapter_num") or row.key or 0)
                if chapter_num <= 0 or chapter_num >= before_chapter:
                    continue
                result.append(dict(row.content))
            result.sort(key=lambda item: int(item.get("chapter_num") or 0))
            return result[-max(1, limit):]
        finally:
            if should_close:
                db.close()

    def get_previous_transition(
        self,
        novel_id: int,
        chapter_num: int,
        *,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> dict[str, Any] | None:
        if chapter_num <= 1:
            return None
        return self._get_memory(
            novel_id,
            "chapter_transition",
            str(chapter_num - 1),
            novel_version_id=novel_version_id,
            db=db,
        )

    def save_chapter_advancement(
        self,
        novel_id: int,
        chapter_num: int,
        content: dict[str, Any],
        *,
        novel_version_id: int | None = None,
        volume_no: int | None = None,
        action: str = "upsert",
        source_chapter_num: int | None = None,
        promotion_score: float | None = None,
        db: Optional[Session] = None,
    ) -> None:
        payload = {"chapter_num": chapter_num, **dict(content or {})}
        if volume_no is not None:
            payload["volume_no"] = int(volume_no)
        self._upsert_memory(
            novel_id,
            "chapter_advancement",
            str(chapter_num),
            payload,
            novel_version_id=novel_version_id,
            action=action,
            source_chapter_num=source_chapter_num or chapter_num,
            promotion_score=promotion_score,
            db=db,
        )

    def save_chapter_transition(
        self,
        novel_id: int,
        chapter_num: int,
        content: dict[str, Any],
        *,
        novel_version_id: int | None = None,
        volume_no: int | None = None,
        action: str = "upsert",
        source_chapter_num: int | None = None,
        promotion_score: float | None = None,
        db: Optional[Session] = None,
    ) -> None:
        payload = {"chapter_num": chapter_num, **dict(content or {})}
        if volume_no is not None:
            payload["volume_no"] = int(volume_no)
        self._upsert_memory(
            novel_id,
            "chapter_transition",
            str(chapter_num),
            payload,
            novel_version_id=novel_version_id,
            action=action,
            source_chapter_num=source_chapter_num or chapter_num,
            promotion_score=promotion_score,
            db=db,
        )

    def get_volume_arc_state(
        self,
        novel_id: int,
        volume_no: int,
        *,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> dict[str, Any] | None:
        return self._get_memory(
            novel_id,
            "volume_arc_state",
            f"volume:{int(volume_no)}",
            novel_version_id=novel_version_id,
            db=db,
        )

    def save_volume_arc_state(
        self,
        novel_id: int,
        volume_no: int,
        content: dict[str, Any],
        *,
        novel_version_id: int | None = None,
        action: str = "upsert",
        source_chapter_num: int | None = None,
        promotion_score: float | None = None,
        db: Optional[Session] = None,
    ) -> None:
        self._upsert_memory(
            novel_id,
            "volume_arc_state",
            f"volume:{int(volume_no)}",
            dict(content or {}),
            novel_version_id=novel_version_id,
            action=action,
            source_chapter_num=source_chapter_num,
            promotion_score=promotion_score,
            db=db,
        )

    def get_book_progression_state(
        self,
        novel_id: int,
        *,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> dict[str, Any] | None:
        return self._get_memory(
            novel_id,
            "book_progression",
            "book",
            novel_version_id=novel_version_id,
            db=db,
        )

    def save_book_progression_state(
        self,
        novel_id: int,
        content: dict[str, Any],
        *,
        novel_version_id: int | None = None,
        action: str = "upsert",
        source_chapter_num: int | None = None,
        promotion_score: float | None = None,
        db: Optional[Session] = None,
    ) -> None:
        self._upsert_memory(
            novel_id,
            "book_progression",
            "book",
            dict(content or {}),
            novel_version_id=novel_version_id,
            action=action,
            source_chapter_num=source_chapter_num,
            promotion_score=promotion_score,
            db=db,
        )

    def list_chapter_advancements(
        self,
        novel_id: int,
        *,
        novel_version_id: int | None = None,
        up_to_chapter: int | None = None,
        db: Optional[Session] = None,
    ) -> list[dict[str, Any]]:
        should_close = db is None
        db = db or SessionLocal()
        try:
            chapter_key = cast(NovelMemory.key, Integer)
            stmt = select(NovelMemory).where(
                NovelMemory.novel_id == novel_id,
                NovelMemory.memory_type == "chapter_advancement",
                chapter_key > 0,
            )
            if novel_version_id is not None:
                stmt = stmt.where(NovelMemory.novel_version_id == novel_version_id)
            if up_to_chapter is not None:
                stmt = stmt.where(chapter_key <= int(up_to_chapter))
            stmt = stmt.order_by(chapter_key.asc(), NovelMemory.id.asc())
            rows = db.execute(stmt).scalars().all()
            result: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row.content, dict):
                    continue
                payload = dict(row.content)
                payload.setdefault("chapter_num", _chapter_num_from_key(row.key))
                result.append(payload)
            return result
        finally:
            if should_close:
                db.close()

    def merge_volume_arc_state(
        self,
        novel_id: int,
        volume_no: int,
        chapter_num: int,
        advancement: dict[str, Any],
        *,
        novel_version_id: int | None = None,
        action: str = "upsert",
        promotion_score: float | None = None,
        db: Optional[Session] = None,
    ) -> dict[str, Any]:
        current = self.get_volume_arc_state(novel_id, volume_no, novel_version_id=novel_version_id, db=db) or {}
        completed_objectives = _string_list(current.get("completed_objectives")) + _string_list(advancement.get("chapter_objective"))
        revealed_information = _string_list(current.get("revealed_information")) + _string_list(advancement.get("new_information"))
        relationship_changes = _string_list(current.get("relationship_changes")) + _string_list(advancement.get("relationship_delta"))
        forbidden_repeats = _string_list(current.get("forbidden_repeats")) + _string_list(advancement.get("forbidden_repeats"))
        payoff_kinds = _string_list(current.get("payoff_kinds")) + _string_list(advancement.get("payoff_kind"))
        reveal_kinds = _string_list(current.get("reveal_kinds")) + _string_list(advancement.get("reveal_kind"))
        conflict_axes = _string_list(current.get("conflict_axes")) + _string_list(advancement.get("conflict_axis"))
        main_conflict = _clean_text(current.get("main_conflict") or advancement.get("conflict_axis"))
        payload = {
            "volume_no": int(volume_no),
            "last_updated_chapter": int(chapter_num),
            "main_conflict": main_conflict[:180],
            "current_phase": _clean_text(advancement.get("phase"))[:120],
            "completed_objectives": _string_list(completed_objectives)[:18],
            "revealed_information": _string_list(revealed_information)[:20],
            "relationship_changes": _string_list(relationship_changes)[:18],
            "forbidden_repeats": _string_list(forbidden_repeats)[:20],
            "payoff_kinds": _string_list(payoff_kinds)[:12],
            "reveal_kinds": _string_list(reveal_kinds)[:12],
            "conflict_axes": _string_list(conflict_axes)[:12],
            "recent_unresolved_threads": _string_list(advancement.get("new_unresolved_threads"))[:10],
            "resolved_threads": _string_list(current.get("resolved_threads"))[:10] + _string_list(advancement.get("resolved_threads"))[:10],
        }
        self.save_volume_arc_state(
            novel_id,
            volume_no,
            payload,
            novel_version_id=novel_version_id,
            action=action,
            source_chapter_num=chapter_num,
            promotion_score=promotion_score,
            db=db,
        )
        return payload

    def merge_book_progression_state(
        self,
        novel_id: int,
        chapter_num: int,
        advancement: dict[str, Any],
        *,
        novel_version_id: int | None = None,
        action: str = "upsert",
        promotion_score: float | None = None,
        db: Optional[Session] = None,
    ) -> dict[str, Any]:
        current = self.get_book_progression_state(novel_id, novel_version_id=novel_version_id, db=db) or {}
        major_beats = _string_list(current.get("major_beats")) + _string_list(advancement.get("major_beats"))
        revealed_information = _string_list(current.get("revealed_information")) + _string_list(advancement.get("new_information"))
        payoff_kinds = _string_list(current.get("payoff_kinds")) + _string_list(advancement.get("payoff_kind"))
        reveal_kinds = _string_list(current.get("reveal_kinds")) + _string_list(advancement.get("reveal_kind"))
        forbidden_repeats = _string_list(current.get("forbidden_repeats")) + _string_list(advancement.get("forbidden_repeats"))
        relationship_state = _string_list(current.get("relationship_state")) + _string_list(advancement.get("relationship_delta"))
        payload = {
            "last_updated_chapter": int(chapter_num),
            "mainline_stage": _clean_text(current.get("mainline_stage") or advancement.get("phase"))[:120],
            "revealed_information": _string_list(revealed_information)[:30],
            "major_beats": _string_list(major_beats)[:30],
            "payoff_kinds": _string_list(payoff_kinds)[:16],
            "reveal_kinds": _string_list(reveal_kinds)[:16],
            "forbidden_repeats": _string_list(forbidden_repeats)[:30],
            "relationship_state": _string_list(relationship_state)[:30],
            "core_conflict_axes": _string_list(current.get("core_conflict_axes")) + _string_list(advancement.get("conflict_axis"))[:10],
        }
        payload["core_conflict_axes"] = _string_list(payload["core_conflict_axes"])[:16]
        self.save_book_progression_state(
            novel_id,
            payload,
            novel_version_id=novel_version_id,
            action=action,
            source_chapter_num=chapter_num,
            promotion_score=promotion_score,
            db=db,
        )
        return payload


def build_anti_repeat_constraints(
    recent_advancements: list[dict[str, Any]],
    volume_arc_state: dict[str, Any] | None,
    book_progression_state: dict[str, Any] | None,
    outline_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    recent_objectives = [
        _clean_text(item.get("chapter_objective"))
        for item in recent_advancements
        if isinstance(item, dict)
    ]
    recent_reveals: list[str] = []
    recent_relationship_deltas: list[str] = []
    recent_forbidden: list[str] = []
    for item in recent_advancements:
        if not isinstance(item, dict):
            continue
        recent_reveals.extend(_string_list(item.get("new_information")))
        recent_relationship_deltas.extend(_string_list(item.get("relationship_delta")))
        recent_forbidden.extend(_string_list(item.get("forbidden_repeats")))
    volume_state = volume_arc_state or {}
    book_state = book_progression_state or {}
    outline_data = outline_contract or {}
    current_objective = _clean_text(outline_data.get("chapter_objective"))
    return {
        "current_objective": current_objective,
        "recent_objectives": _string_list(recent_objectives)[:8],
        "recent_reveals": _string_list(recent_reveals)[:10],
        "recent_relationship_deltas": _string_list(recent_relationship_deltas)[:10],
        "recent_forbidden_repeats": _string_list(recent_forbidden)[:10],
        "volume_payoff_kinds": _string_list(volume_state.get("payoff_kinds"))[:8],
        "volume_reveal_kinds": _string_list(volume_state.get("reveal_kinds"))[:8],
        "volume_forbidden_repeats": _string_list(volume_state.get("forbidden_repeats"))[:12],
        "book_major_beats": _string_list(book_state.get("major_beats"))[:12],
        "book_revealed_information": _string_list(book_state.get("revealed_information"))[:12],
        "book_forbidden_repeats": _string_list(book_state.get("forbidden_repeats"))[:14],
    }


def build_transition_constraints(previous_transition_state: dict[str, Any] | None) -> dict[str, Any]:
    prev = previous_transition_state or {}
    ending_scene = _clean_text(prev.get("ending_scene"))
    last_action = _clean_text(prev.get("last_action"))
    time_state = _clean_text(prev.get("time_state"))
    scene_exit = _clean_text(prev.get("scene_exit"))
    constraints: list[str] = []
    if ending_scene:
        constraints.append(f"上一章结束场景：{ending_scene}")
    if last_action:
        constraints.append(f"上一章最后动作：{last_action}")
    if time_state:
        constraints.append(f"上一章时间状态：{time_state}")
    if scene_exit:
        constraints.append(f"上一章场景出口：{scene_exit}")
    if constraints:
        constraints.append("若本章开头跳场或跳时，必须显式交代过渡，不能无说明回到不连续状态。")
    return {
        "previous_transition_state": prev,
        "opening_constraints": constraints[:6],
    }


def similarity_against_constraints(value: str, candidates: list[str], threshold: float = 0.82) -> tuple[bool, str]:
    best_match = ""
    best_score = 0.0
    for candidate in candidates:
        score = _best_similarity(value, [candidate])
        if score > best_score:
            best_score = score
            best_match = candidate
    return best_score >= threshold, best_match
