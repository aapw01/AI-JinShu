"""Wave 2 LLM agents (#4 / #5 / #7 / #8 / #12) with mocked LLM."""

from __future__ import annotations

from typing import Any

import pytest

from app.core import feature_flags
from app.core.database import SessionLocal
from app.models.novel import (
    Novel,
    NovelVersion,
    OutlineAuditReportRow,
    ReaderLensReportRow,
    SpacetimeAnchorRow,
    VoiceFingerprintRow,
)
from app.services.agents import (
    outline_auditor,
    patch_writer,
    reader_lens,
    spacetime_extractor,
    voice_drift,
)
from app.services.agents import llm_agent as llm_agent_module
from app.services.agents.contracts.outline import OutlineContract
from app.services.agents.contracts.patch import EditSpan, PatchInstruction


@pytest.fixture(autouse=True)
def _clear():
    feature_flags.invalidate_flags_cache()
    db = SessionLocal()
    try:
        for cls in (
            SpacetimeAnchorRow,
            VoiceFingerprintRow,
            OutlineAuditReportRow,
            ReaderLensReportRow,
        ):
            db.query(cls).delete()
        db.commit()
    finally:
        db.close()
    yield
    feature_flags.invalidate_flags_cache()


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_novel(session) -> tuple[Novel, NovelVersion]:
    n = Novel(title="t")
    session.add(n)
    session.commit()
    nv = NovelVersion(novel_id=n.id, version_no=1, status="draft")
    session.add(nv)
    session.commit()
    return n, nv


def _enable(name: str):
    feature_flags.set_flag(
        name, enabled=True, rollout_pct=100, changed_by="test", reason="enable"
    )


def _patch_run_llm(monkeypatch, payload: dict[str, Any]):
    """让 ``run_llm_agent`` 返回固定 payload（绕开真实 LLM）。"""

    def _fake(*, schema, **_kwargs):
        return schema.model_validate(payload)

    monkeypatch.setattr(llm_agent_module, "run_llm_agent", _fake)
    # 同步 patch 各 agent 模块里的 import 引用
    for module in (
        spacetime_extractor,
        voice_drift,
        outline_auditor,
        patch_writer,
        reader_lens,
    ):
        if hasattr(module, "run_llm_agent"):
            monkeypatch.setattr(module, "run_llm_agent", _fake)


# --- #4 ---------------------------------------------------------------------
def test_spacetime_flag_off_returns_none(session):
    n, nv = _make_novel(session)
    out = spacetime_extractor.extract_and_persist(
        session,
        novel_id=n.id,
        novel_version_id=nv.id,
        chapter_num=3,
        chapter_text="次日清晨，他们抵达县城。",
    )
    assert out is None


def test_spacetime_flag_on_persists(session, monkeypatch):
    n, nv = _make_novel(session)
    _enable("consistency.spacetime_v1")
    _patch_run_llm(
        monkeypatch,
        {
            "schema_version": "v1",
            "chapter_num": 0,  # will be overridden
            "when": "次日清晨",
            "where": "县城",
            "who": ["K001"],
            "duration_minutes": 60,
            "relative_to_prev": "after",
        },
    )
    out = spacetime_extractor.extract_and_persist(
        session,
        novel_id=n.id,
        novel_version_id=nv.id,
        chapter_num=3,
        chapter_text="次日清晨，他们抵达县城。",
    )
    assert out is not None
    assert out.chapter_num == 3
    row = session.query(SpacetimeAnchorRow).filter_by(chapter_num=3).one()
    assert row.where_text == "县城"


# --- #5 ---------------------------------------------------------------------
def test_voice_drift_no_dialogue_returns_none(session):
    n, nv = _make_novel(session)
    _enable("quality.voice_drift_audit")
    out = voice_drift.audit_chapter_voice(
        session,
        novel_id=n.id,
        novel_version_id=nv.id,
        chapter_num=2,
        character_key="K001",
        chapter_text="他走进屋子，没人说话。",
    )
    assert out is None


def test_voice_drift_first_seed_only(session):
    n, nv = _make_novel(session)
    _enable("quality.voice_drift_audit")
    out = voice_drift.audit_chapter_voice(
        session,
        novel_id=n.id,
        novel_version_id=nv.id,
        chapter_num=1,
        character_key="K001",
        chapter_text="他笑道：“阁下何意？请明示。”",
    )
    assert out is None  # first chapter only seeds baseline
    fp = session.query(VoiceFingerprintRow).filter_by(character_key="K001").one()
    assert fp.formality_score >= 0.5


def test_voice_drift_with_history_calls_llm(session, monkeypatch):
    n, nv = _make_novel(session)
    _enable("quality.voice_drift_audit")
    session.add(
        VoiceFingerprintRow(
            novel_version_id=nv.id,
            character_key="K001",
            avg_sentence_len=5.0,
            formality_score=0.7,
            register="high",
            sample_chapter_from=1,
            sample_chapter_to=1,
        )
    )
    session.commit()
    _patch_run_llm(
        monkeypatch,
        {"drift_score": 0.6, "triggered": True, "diff_dimensions": ["formality_score"]},
    )
    out = voice_drift.audit_chapter_voice(
        session,
        novel_id=n.id,
        novel_version_id=nv.id,
        chapter_num=2,
        character_key="K001",
        chapter_text="他骂道：“靠，又来一次！”",
    )
    assert out is not None
    assert out.triggered is True
    assert "formality_score" in out.diff_dimensions


def test_voice_statistical_helpers():
    dialogues = voice_drift.extract_dialogues("他说：“阁下何意？”然后又问：“先生贵姓？”")
    assert dialogues == ["阁下何意？", "先生贵姓？"]
    fp = voice_drift.compute_statistical_fingerprint(dialogues)
    assert fp["voice_register"] in ("high", "neutral")


# --- #7 ---------------------------------------------------------------------
def test_outline_auditor_persists(session, monkeypatch):
    n, nv = _make_novel(session)
    _enable("quality.outline_promise_audit")
    _patch_run_llm(
        monkeypatch,
        {
            "schema_version": "v1",
            "chapter_num": 3,
            "promises": [
                {"key": "objective", "fulfilled": "yes"},
                {"key": "payoff", "fulfilled": "no", "note": "missing"},
            ],
            "must_fix_count": 1,
        },
    )
    contract = OutlineContract(
        chapter_num=3,
        chapter_objective="揭开身份谜底",
        required_new_information=["A 真名"],
        payoff="A 自称结束",
    )
    out = outline_auditor.audit_chapter_outline(
        session,
        novel_id=n.id,
        novel_version_id=nv.id,
        chapter_num=3,
        contract=contract,
        chapter_text="A 走进房间……",
    )
    assert out is not None
    assert out.must_fix_count == 1
    row = session.query(OutlineAuditReportRow).filter_by(chapter_num=3).one()
    assert row.must_fix_count == 1


# --- #8 ---------------------------------------------------------------------
def test_patch_writer_validates_length_delta(monkeypatch):
    _enable("repair.precision_rewrite")
    _patch_run_llm(
        monkeypatch,
        {
            "schema_version": "v1",
            "patched_text": "X" * 200,  # original is 10 chars → ratio=19, exceeds 0.3
            "length_delta": 190,
            "introduces_new_characters": [],
        },
    )
    instr = PatchInstruction(
        span=EditSpan(
            span_start=0,
            span_end=10,
            anchor_before="A",
            anchor_after="B",
            original_text="0123456789",
        ),
        instruction="rewrite",
    )
    out = patch_writer.apply_patch(
        novel_id=1,
        novel_version_id=None,
        chapter_num=1,
        instruction=instr,
        chapter_text="...",
    )
    assert out is None  # rejected due to length explosion


def test_patch_writer_accepts_clean_patch(monkeypatch):
    _enable("repair.precision_rewrite")
    _patch_run_llm(
        monkeypatch,
        {
            "schema_version": "v1",
            "patched_text": "0123456789AB",
            "length_delta": 2,
            "introduces_new_characters": [],
        },
    )
    instr = PatchInstruction(
        span=EditSpan(
            span_start=0,
            span_end=20,
            anchor_before="A",
            anchor_after="B",
            original_text="01234567890123456789",
        ),
        instruction="rewrite",
    )
    out = patch_writer.apply_patch(
        novel_id=1,
        novel_version_id=None,
        chapter_num=1,
        instruction=instr,
        chapter_text="...",
    )
    assert out is not None
    assert out.length_delta == 2


def test_patch_writer_rejects_new_characters(monkeypatch):
    _enable("repair.precision_rewrite")
    _patch_run_llm(
        monkeypatch,
        {
            "schema_version": "v1",
            "patched_text": "abc",
            "length_delta": 0,
            "introduces_new_characters": ["X"],
        },
    )
    instr = PatchInstruction(
        span=EditSpan(
            span_start=0,
            span_end=3,
            anchor_before="A",
            anchor_after="B",
            original_text="xyz",
        ),
        instruction="rewrite",
        forbid_new_characters=True,
    )
    out = patch_writer.apply_patch(
        novel_id=1, novel_version_id=None, chapter_num=1, instruction=instr, chapter_text="..."
    )
    assert out is None


# --- #12 --------------------------------------------------------------------
def test_reader_lens_persists(session, monkeypatch):
    n, nv = _make_novel(session)
    _enable("quality.reader_lens_audit")
    _patch_run_llm(
        monkeypatch,
        {
            "first_read_fluency": 0.78,
            "info_density": 0.55,
            "missing_setups": ["A 的来历"],
        },
    )
    out = reader_lens.evaluate_chapter(
        session,
        novel_id=n.id,
        novel_version_id=nv.id,
        chapter_num=4,
        chapter_text="A 突然出现……",
        prior_summary="此前从未提及 A。",
        model_label="gpt-4o",
    )
    assert out is not None
    assert out.first_read_fluency == 0.78
    row = session.query(ReaderLensReportRow).filter_by(chapter_num=4).one()
    assert row.model == "gpt-4o"
    assert "A 的来历" in (row.missing_setups or [])
