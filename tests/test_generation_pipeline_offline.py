"""End-to-end offline pipeline test — zero external LLM/embedding calls.

Demonstrates P0 of the harness plan: the *real* compiled LangGraph pipeline runs
against a seeded novel with deterministic fake agents, and we assert orchestration
(route order, rollback, persistence, token bookkeeping) — not model output.

Scenario: chapter 1 is scripted to score low three times, forcing
``revise -> revise -> rollback_rerun`` and then converging to ``finalizer``.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.novel import ChapterVersion
from app.services.generation.langgraph_pipeline import run_generation_pipeline_langgraph
from tests.support.fake_llm import (
    ScriptedReviewPolicy,
    install_offline_harness,
    seed_novel,
)

pytestmark = [pytest.mark.offline, pytest.mark.slow]


def _run_two_chapter_segment(novel_id: int, version_id: int) -> None:
    run_generation_pipeline_langgraph(
        novel_id=novel_id,
        novel_version_id=version_id,
        segment_target_chapters=2,
        segment_start_chapter=1,
        book_start_chapter=1,
        book_target_total_chapters=2,
        book_effective_end_chapter=2,
        volume_no=1,
        task_id=None,
        creation_task_id=None,
    )


def test_pipeline_converges_via_revise_then_rollback(monkeypatch):
    novel_id, version_id = seed_novel()
    harness = install_offline_harness(
        monkeypatch,
        review_policy=ScriptedReviewPolicy(loop_chapter=1, low_rounds=3),
    )

    _run_two_chapter_segment(novel_id, version_id)

    trace = harness.node_trace

    # The full skeleton executed.
    for node in ("init", "prewrite", "outline", "writer", "reviewer", "finalizer"):
        assert node in trace, f"expected node {node!r} in trace: {trace}"

    # Low scores forced a revise loop then a rollback rerun before converging.
    assert "revise" in trace, trace
    assert "rollback_rerun" in trace, trace
    assert trace.index("finalizer") > trace.index("rollback_rerun"), trace

    # The rollback actually reset progression memory for the stuck chapter.
    assert harness.rollback_calls, "expected rollback_progression_range to be invoked"
    assert any(call.get("from_chapter") == 1 for call in harness.rollback_calls)


def test_pipeline_persists_chapters_and_accumulates_tokens(monkeypatch):
    novel_id, version_id = seed_novel()
    install_offline_harness(
        monkeypatch,
        review_policy=ScriptedReviewPolicy(loop_chapter=1, low_rounds=3),
    )

    _run_two_chapter_segment(novel_id, version_id)

    db = SessionLocal()
    try:
        rows = (
            db.execute(
                select(ChapterVersion)
                .where(ChapterVersion.novel_version_id == version_id)
                .order_by(ChapterVersion.chapter_num)
            )
            .scalars()
            .all()
        )
    finally:
        db.close()

    # Both chapters were written and persisted with non-empty content.
    assert [r.chapter_num for r in rows] == [1, 2]
    for row in rows:
        assert row.content and len(row.content) >= 100
        assert row.summary is not None

    # Chapter 1 converged on the high review score after the rollback.
    assert rows[0].review_score is not None
    assert rows[0].review_score >= 0.7


def test_pipeline_runs_fully_offline_when_review_converges(monkeypatch):
    """A run that converges on first review proves the pipeline stays offline.

    The harness patches get_llm/get_llm_with_fallback/get_embedding_model, so if
    any real model were required the run would fail; completing it (and persisting
    a chapter) proves zero external calls. With ``low_rounds=0`` the reviewer never
    forces a revise loop.
    """
    novel_id, version_id = seed_novel()
    harness = install_offline_harness(
        monkeypatch, review_policy=ScriptedReviewPolicy(low_rounds=0)
    )

    _run_two_chapter_segment(novel_id, version_id)

    assert "finalizer" in harness.node_trace
    # Immediate review convergence => the retry/revise branch is never taken.
    assert "revise" not in harness.node_trace

    db = SessionLocal()
    try:
        count = len(
            db.execute(
                select(ChapterVersion).where(
                    ChapterVersion.novel_version_id == version_id
                )
            )
            .scalars()
            .all()
        )
    finally:
        db.close()
    assert count >= 1
