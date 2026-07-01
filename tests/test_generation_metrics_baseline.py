"""Offline metrics baseline regression (P4).

Runs a deterministic segment and pins its stability metrics against a committed
baseline. If a change makes runs non-convergent, drop chapters, or blow up the
retry/rollback loops, ``check_baseline`` will surface it here — with no external
model call.
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
from tests.support.metrics import DEFAULT_BASELINE, check_baseline, summarize_run

pytestmark = [pytest.mark.offline, pytest.mark.slow]

_CHAPTERS = 6
_SCHEDULE = {3: 3}


def _chapters(version_id: int) -> list[ChapterVersion]:
    db = SessionLocal()
    try:
        return list(
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


def test_offline_run_meets_metrics_baseline(monkeypatch):
    novel_id, version_id = seed_novel(title="P4 Metrics Novel")
    harness = install_offline_harness(
        monkeypatch, review_policy=ScriptedReviewPolicy(schedule=dict(_SCHEDULE))
    )

    run_generation_pipeline_langgraph(
        novel_id=novel_id,
        novel_version_id=version_id,
        segment_target_chapters=_CHAPTERS,
        segment_start_chapter=1,
        book_start_chapter=1,
        book_target_total_chapters=_CHAPTERS,
        book_effective_end_chapter=_CHAPTERS,
        volume_no=1,
        task_id=None,
        creation_task_id=None,
    )

    metrics = summarize_run(
        node_trace=harness.node_trace,
        rollback_calls=harness.rollback_calls,
        chapters=_chapters(version_id),
    )

    # Baseline gate (the same check CI enforces).
    violations = check_baseline(metrics, DEFAULT_BASELINE)
    assert violations == [], (violations, metrics)

    # A few explicit pins on top of the gate.
    assert metrics["chapters_total"] == _CHAPTERS
    assert metrics["all_converged"] is True
    assert metrics["writer_per_chapter"] <= DEFAULT_BASELINE["max_writer_per_chapter"]
    # The scripted hard chapter must have driven at least one rollback.
    assert metrics["rollback_invocations"] >= 1


def test_check_baseline_flags_a_regression():
    """The gate is a real check: degraded metrics must produce violations."""
    bad = {
        "chapters_total": 5,
        "chapters_with_content": 3,
        "chapters_in_order": False,
        "writer_calls": 60,
        "reviewer_calls": 0,
        "review_score_min": 0.2,
        "all_converged": False,
        "writer_per_chapter": 12.0,
        "reviewer_per_chapter": 0.0,
    }
    violations = check_baseline(bad, DEFAULT_BASELINE)
    assert len(violations) >= 4
