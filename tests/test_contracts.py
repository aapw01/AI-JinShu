"""Contract smoke tests (Phase 0 §4.3)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.agents.contracts import (
    BlockerEntry,
    ConsistencyReportV2,
    FactArbitrationDecision,
    FactRecord,
    OutlineAuditReport,
    OutlineContract,
    OutlinePromiseVerdict,
    PatchInstruction,
    EditSpan,
    ReaderLensVerdict,
    SpacetimeAnchor,
    VoiceDriftReport,
    VoiceFingerprint,
)
from app.services.agents.contracts.events import (
    EVENT_PAYLOAD_REGISTRY,
    install_into_emitter,
)


def test_consistency_v2_roundtrip():
    rep = ConsistencyReportV2(
        chapter_num=3,
        blockers=[BlockerEntry(category="hard_constraint", message="X cannot be Y")],
        outline_revise_attempts=1,
    )
    js = rep.model_dump()
    assert js["schema_version"] == "v2"
    assert js["blockers"][0]["category"] == "hard_constraint"


def test_fact_record_confidence_bounds():
    with pytest.raises(ValidationError):
        FactRecord(
            novel_id=1,
            entity_id=1,
            fact_type="status",
            chapter_from=1,
            source_chapter=1,
            confidence=1.5,
        )


def test_outline_audit_report():
    rep = OutlineAuditReport(
        chapter_num=2,
        promises=[
            OutlinePromiseVerdict(key="payoff", fulfilled="partial", note="weak"),
        ],
        must_fix_count=0,
    )
    assert rep.promises[0].fulfilled == "partial"


def test_patch_instruction_extra_forbidden():
    span = EditSpan(span_start=0, span_end=10, anchor_before="A", anchor_after="B", original_text="hello")
    with pytest.raises(ValidationError):
        PatchInstruction(span=span, instruction="fix", unknown_field=1)  # type: ignore[arg-type]


def test_event_registry_installs_into_emitter():
    assert ("consistency_check", "revise_attempt") in EVENT_PAYLOAD_REGISTRY
    install_into_emitter()
    from app.services.agents import events as ev

    assert ("consistency_check", "revise_attempt") in ev._EVENT_PAYLOAD_REGISTRY


def test_unused_contracts_smoke():
    """Smoke check that all top-level contracts can construct minimal valid payload."""
    SpacetimeAnchor(chapter_num=1)
    VoiceFingerprint(character_key="X", avg_sentence_len=10.0, formality_score=0.5)
    VoiceDriftReport(
        character_key="X", drift_score=0.1, threshold=0.35, triggered=False
    )
    ReaderLensVerdict(
        chapter_num=1,
        first_read_fluency=0.7,
        info_density=0.5,
        model="test",
        sampled_at_chapter=1,
    )
    OutlineContract(chapter_num=1, chapter_objective="x")
    FactArbitrationDecision(decision="keep", reason="single fact")
