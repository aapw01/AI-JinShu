"""Promotion and rollback controls for progression memory."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.services.memory.progression_state import (
    ProgressionMemoryManager,
    _best_similarity,
    normalize_outline_contract,
)

ADVANCEMENT_PROMOTION_THRESHOLD = 0.72
TRANSITION_PROMOTION_THRESHOLD = 0.80


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw = value
    else:
        raw = [value]
    out: list[str] = []
    for item in raw:
        text = _clean_text(item)
        if text and text not in out:
            out.append(text)
    return out


def _clamp_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value or 0.0)))
    except Exception:
        return 0.0


def _fallback_advancement_confidence(advancement: dict[str, Any]) -> float:
    signals = 0
    if _clean_text(advancement.get("actual_progress")):
        signals += 1
    if _string_list(advancement.get("new_information")):
        signals += 1
    if _clean_text(advancement.get("irreversible_change")):
        signals += 1
    if _clean_text(advancement.get("relationship_delta")):
        signals += 1
    if signals >= 3:
        return 0.78
    if signals >= 2:
        return 0.74
    if signals >= 1:
        return 0.66
    return 0.0


def _fallback_transition_confidence(transition: dict[str, Any]) -> float:
    if not _clean_text(transition.get("ending_scene")):
        return 0.0
    signals = 0
    for field in ("last_action", "scene_exit", "unresolved_exit", "physical_state", "time_state"):
        if _clean_text(transition.get(field)):
            signals += 1
    if _string_list(transition.get("character_positions")):
        signals += 1
    if signals >= 3:
        return 0.84
    if signals >= 2:
        return 0.8
    if signals >= 1:
        return 0.72
    return 0.0


def _has_effective_advancement(advancement: dict[str, Any]) -> bool:
    return any(
        [
            _clean_text(advancement.get("actual_progress")),
            bool(_string_list(advancement.get("new_information"))),
            _clean_text(advancement.get("irreversible_change")),
            _clean_text(advancement.get("relationship_delta")),
        ]
    )


def _has_effective_transition(transition: dict[str, Any]) -> bool:
    if not _clean_text(transition.get("ending_scene")):
        return False
    return any(
        [
            _clean_text(transition.get("last_action")),
            _clean_text(transition.get("scene_exit")),
            _clean_text(transition.get("unresolved_exit")),
        ]
    )


def _outline_alignment_failures(
    advancement: dict[str, Any],
    outline_contract: dict[str, Any],
) -> list[str]:
    if not advancement:
        return []
    failures: list[str] = []

    outline_objective = _clean_text(outline_contract.get("chapter_objective"))
    extracted_objective = _clean_text(
        advancement.get("chapter_objective")
        or advancement.get("actual_progress")
        or advancement.get("conflict_axis")
    )
    if outline_objective and extracted_objective:
        similarity = max(
            _best_similarity(extracted_objective, [outline_objective]),
            _best_similarity(_clean_text(advancement.get("actual_progress")), [outline_objective]),
        )
        if similarity < 0.42:
            failures.append("advancement_outline_objective_mismatch")

    outline_conflict = _clean_text(outline_contract.get("conflict_axis"))
    extracted_conflict = _clean_text(advancement.get("conflict_axis"))
    if outline_conflict and extracted_conflict:
        if _best_similarity(extracted_conflict, [outline_conflict]) < 0.45:
            failures.append("advancement_outline_conflict_mismatch")

    outline_payoff_kind = _clean_text(outline_contract.get("payoff_kind"))
    extracted_payoff_kind = _clean_text(advancement.get("payoff_kind"))
    if (
        outline_payoff_kind
        and extracted_payoff_kind
        and outline_payoff_kind != extracted_payoff_kind
        and outline_payoff_kind != "general_payoff"
        and extracted_payoff_kind != "general_payoff"
    ):
        failures.append("advancement_outline_payoff_kind_mismatch")

    outline_reveal_kind = _clean_text(outline_contract.get("reveal_kind"))
    extracted_reveal_kind = _clean_text(advancement.get("reveal_kind"))
    if (
        outline_reveal_kind
        and extracted_reveal_kind
        and outline_reveal_kind != extracted_reveal_kind
        and outline_reveal_kind != "general_reveal"
        and extracted_reveal_kind != "general_reveal"
    ):
        failures.append("advancement_outline_reveal_kind_mismatch")

    required_information = _string_list(outline_contract.get("required_new_information"))
    extracted_information = _string_list(advancement.get("new_information"))
    if required_information:
        if not extracted_information:
            failures.append("advancement_outline_missing_required_information")
        else:
            joined = extracted_information + [
                _clean_text(advancement.get("actual_progress")),
                _clean_text(advancement.get("irreversible_change")),
            ]
            max_similarity = 0.0
            for required in required_information:
                max_similarity = max(max_similarity, _best_similarity(required, joined))
            if max_similarity < 0.38:
                failures.append("advancement_outline_information_mismatch")

    return failures


class ProgressionPromotionService:
    """Decide whether extracted progression memory is safe to promote."""

    def decide(
        self,
        *,
        chapter_num: int,
        extraction: dict[str, Any] | None,
        outline_contract: dict[str, Any] | None,
        review_suggestions: dict[str, Any] | None,
        review_gate: dict[str, Any] | None,
    ) -> dict[str, Any]:
        raw_payload = dict(extraction or {})
        advancement = raw_payload.get("advancement") if isinstance(raw_payload.get("advancement"), dict) else {}
        transition = raw_payload.get("transition") if isinstance(raw_payload.get("transition"), dict) else {}
        normalized_outline = normalize_outline_contract(outline_contract or {}, chapter_num)
        review_suggestions = review_suggestions or {}
        scorecards = review_suggestions.get("scorecards") if isinstance(review_suggestions.get("scorecards"), dict) else {}
        progression_scorecard = scorecards.get("progression") if isinstance(scorecards.get("progression"), dict) else {}
        factual_scorecard = scorecards.get("factual") if isinstance(scorecards.get("factual"), dict) else {}
        gate = review_gate or {}
        validation_notes = _string_list(raw_payload.get("validation_notes"))
        blocked_reasons: list[str] = []

        advancement_confidence = _clamp_confidence(raw_payload.get("advancement_confidence"))
        transition_confidence = _clamp_confidence(raw_payload.get("transition_confidence"))
        if advancement_confidence <= 0.0 and advancement:
            advancement_confidence = _fallback_advancement_confidence(advancement)
            if advancement_confidence > 0:
                validation_notes.append("advancement_confidence_fallback_applied")
        if transition_confidence <= 0.0 and transition:
            transition_confidence = _fallback_transition_confidence(transition)
            if transition_confidence > 0:
                validation_notes.append("transition_confidence_fallback_applied")

        no_new_delta = _string_list(progression_scorecard.get("no_new_delta"))
        transition_conflict = _string_list(progression_scorecard.get("transition_conflict"))
        transition_conflict.extend(
            str(issue.get("claim") or "").strip()
            for issue in (gate.get("validated_issues") or [])
            if isinstance(issue, dict) and str(issue.get("category") or "") in {"timeline", "continuity"}
        )
        contradictions = _string_list(factual_scorecard.get("contradictions"))

        promote_advancement = True
        if not advancement or not _has_effective_advancement(advancement):
            blocked_reasons.append("advancement_missing_effective_delta")
            promote_advancement = False
        if advancement_confidence < ADVANCEMENT_PROMOTION_THRESHOLD:
            blocked_reasons.append("advancement_low_confidence")
            promote_advancement = False
        if no_new_delta:
            blocked_reasons.append("advancement_blocked_by_review_no_new_delta")
            promote_advancement = False
        outline_failures = _outline_alignment_failures(advancement, normalized_outline)
        if outline_failures:
            blocked_reasons.extend(outline_failures)
            promote_advancement = False

        promote_transition = True
        if not transition or not _has_effective_transition(transition):
            blocked_reasons.append("transition_missing_effective_state")
            promote_transition = False
        if transition_confidence < TRANSITION_PROMOTION_THRESHOLD:
            blocked_reasons.append("transition_low_confidence")
            promote_transition = False
        if transition_conflict:
            blocked_reasons.append("transition_blocked_by_review_conflict")
            promote_transition = False
        elif contradictions and any("衔接" in item or "场景" in item or "时间" in item for item in contradictions):
            blocked_reasons.append("transition_blocked_by_factual_contradiction")
            promote_transition = False

        promoted_payload = {
            "advancement": dict(advancement) if promote_advancement else {},
            "transition": dict(transition) if promote_transition else {},
        }
        promotion_score = 0.0
        promoted_confidences = [
            confidence
            for confidence, enabled in (
                (advancement_confidence, promote_advancement),
                (transition_confidence, promote_transition),
            )
            if enabled
        ]
        if promoted_confidences:
            promotion_score = round(sum(promoted_confidences) / len(promoted_confidences), 4)

        if promote_advancement and promote_transition:
            decision = "promote_all"
        elif promote_advancement:
            decision = "promote_advancement_only"
        elif promote_transition:
            decision = "promote_transition_only"
        else:
            decision = "promote_none"

        return {
            "decision": decision,
            "promotion_score": promotion_score,
            "promote_advancement": promote_advancement,
            "promote_transition": promote_transition,
            "advancement_confidence": advancement_confidence,
            "transition_confidence": transition_confidence,
            "blocked_reasons": blocked_reasons,
            "validation_notes": validation_notes,
            "outline_contract": normalized_outline,
            "review_no_new_delta": no_new_delta,
            "review_transition_conflict": _string_list(transition_conflict),
            "promoted_payload": promoted_payload,
        }


class ProgressionRollbackService:
    """Rollback chapter-level progression memory and rebuild derived states."""

    def __init__(self, manager: ProgressionMemoryManager | None = None) -> None:
        self.manager = manager or ProgressionMemoryManager()

    def rollback_from_chapter(
        self,
        *,
        novel_id: int,
        novel_version_id: int | None,
        from_chapter: int,
        db: Session,
    ) -> dict[str, Any]:
        rollback_floor = int(from_chapter)
        deleted_advancement = self.manager.delete_memories_from_chapter(
            novel_id,
            "chapter_advancement",
            rollback_floor,
            novel_version_id=novel_version_id,
            action="rollback",
            db=db,
        )
        deleted_transition = self.manager.delete_memories_from_chapter(
            novel_id,
            "chapter_transition",
            rollback_floor,
            novel_version_id=novel_version_id,
            action="rollback",
            db=db,
        )
        deleted_volume = 0
        deleted_book = 0
        rebuild = {
            "rebuilt_volume_arc_states": [],
            "rebuilt_book_progression": False,
            "last_rebuilt_chapter": max(rollback_floor - 1, 0),
        }
        if deleted_advancement > 0 or deleted_transition > 0:
            deleted_volume = self.manager.delete_memories_by_type(
                novel_id,
                "volume_arc_state",
                novel_version_id=novel_version_id,
                action="rollback",
                source_chapter_num=max(rollback_floor - 1, 0),
                db=db,
            )
            deleted_book = self.manager.delete_memories_by_type(
                novel_id,
                "book_progression",
                novel_version_id=novel_version_id,
                action="rollback",
                source_chapter_num=max(rollback_floor - 1, 0),
                db=db,
            )
            rebuild = self.rebuild_progression_state(
                novel_id=novel_id,
                novel_version_id=novel_version_id,
                up_to_chapter=max(rollback_floor - 1, 0),
                db=db,
                clear_existing=False,
            )
        return {
            "from_chapter": rollback_floor,
            "deleted_advancement": deleted_advancement,
            "deleted_transition": deleted_transition,
            "deleted_volume_arc_state": deleted_volume,
            "deleted_book_progression": deleted_book,
            "rebuild": rebuild,
        }

    def rebuild_progression_state(
        self,
        *,
        novel_id: int,
        novel_version_id: int | None,
        up_to_chapter: int,
        db: Session,
        clear_existing: bool = True,
    ) -> dict[str, Any]:
        rebuilt_volume_keys: list[str] = []
        rebuilt_book = False
        if clear_existing:
            self.manager.delete_memories_by_type(
                novel_id,
                "volume_arc_state",
                novel_version_id=novel_version_id,
                action="rebuild",
                source_chapter_num=int(up_to_chapter),
                db=db,
            )
            self.manager.delete_memories_by_type(
                novel_id,
                "book_progression",
                novel_version_id=novel_version_id,
                action="rebuild",
                source_chapter_num=int(up_to_chapter),
                db=db,
            )
        rows = self.manager.list_chapter_advancements(
            novel_id,
            novel_version_id=novel_version_id,
            up_to_chapter=int(up_to_chapter),
            db=db,
        )
        for advancement in rows:
            chapter_num = int(advancement.get("chapter_num") or 0)
            if chapter_num <= 0:
                continue
            volume_no = int(advancement.get("volume_no") or max(1, ((chapter_num - 1) // 30) + 1))
            self.manager.merge_volume_arc_state(
                novel_id,
                volume_no,
                chapter_num,
                advancement,
                novel_version_id=novel_version_id,
                action="rebuild",
                promotion_score=1.0,
                db=db,
            )
            self.manager.merge_book_progression_state(
                novel_id,
                chapter_num,
                advancement,
                novel_version_id=novel_version_id,
                action="rebuild",
                promotion_score=1.0,
                db=db,
            )
            key = f"volume:{volume_no}"
            if key not in rebuilt_volume_keys:
                rebuilt_volume_keys.append(key)
            rebuilt_book = True
        return {
            "rebuilt_volume_arc_states": rebuilt_volume_keys,
            "rebuilt_book_progression": rebuilt_book,
            "last_rebuilt_chapter": int(up_to_chapter),
        }


def rollback_progression_range(
    *,
    novel_id: int,
    novel_version_id: int | None,
    from_chapter: int,
    manager: ProgressionMemoryManager | None = None,
) -> dict[str, Any]:
    """Rollback progression memory in a dedicated session and commit atomically."""
    db = SessionLocal()
    try:
        result = ProgressionRollbackService(manager).rollback_from_chapter(
            novel_id=novel_id,
            novel_version_id=novel_version_id,
            from_chapter=from_chapter,
            db=db,
        )
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
