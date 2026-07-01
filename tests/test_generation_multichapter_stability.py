"""Multi-chapter stability regression (P2) — reuses the P0 offline harness.

Runs a longer segment fully offline and asserts *orchestration* invariants that
must hold across a long run: every chapter is written/reviewed/finalized and
persisted in order, scripted "hard" chapters recover via revise -> rollback ->
converge, total retry/rollback work stays bounded (no runaway loops), and the
same script yields reproducible orchestration. This guards long-form run
stability without any external model call.
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

_HARD_CHAPTERS = 8
_HARD_SCHEDULE = {3: 3, 6: 3}  # both exceed max_retries=2 -> force rollback_rerun


def _run_segment(novel_id: int, version_id: int, n: int) -> None:
    run_generation_pipeline_langgraph(
        novel_id=novel_id,
        novel_version_id=version_id,
        segment_target_chapters=n,
        segment_start_chapter=1,
        book_start_chapter=1,
        book_target_total_chapters=n,
        book_effective_end_chapter=n,
        volume_no=1,
        task_id=None,
        creation_task_id=None,
    )


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


@pytest.fixture(scope="module")
def stability_run():
    """One long offline run shared by all hard-schedule assertions (saves time)."""
    mp = pytest.MonkeyPatch()
    novel_id, version_id = seed_novel(title="P2 Stability Novel")
    policy = ScriptedReviewPolicy(schedule=dict(_HARD_SCHEDULE))
    harness = install_offline_harness(mp, review_policy=policy)
    _run_segment(novel_id, version_id, _HARD_CHAPTERS)
    try:
        yield {
            "novel_id": novel_id,
            "version_id": version_id,
            "harness": harness,
            "policy": policy,
            "chapters": _chapters(version_id),
        }
    finally:
        mp.undo()


# --- persistence / convergence --------------------------------------------


def test_all_chapters_persisted_in_order(stability_run):
    rows = stability_run["chapters"]
    assert [r.chapter_num for r in rows] == list(range(1, _HARD_CHAPTERS + 1))
    for row in rows:
        assert row.content and len(row.content) >= 100
        assert row.summary is not None


def test_no_chapter_left_blocked_or_failed(stability_run):
    rows = stability_run["chapters"]
    bad = [(r.chapter_num, r.status) for r in rows if r.status in {"blocked", "failed"}]
    assert not bad, f"chapters left in a bad state: {bad}"


def test_every_chapter_converges_to_high_score(stability_run):
    rows = stability_run["chapters"]
    low = [(r.chapter_num, r.review_score) for r in rows if (r.review_score or 0) < 0.7]
    assert not low, f"chapters that never converged: {low}"


# --- rollback attribution --------------------------------------------------


def test_scripted_hard_chapters_triggered_rollback(stability_run):
    """Scripted low-score chapters must exercise the rollback recovery path."""
    harness = stability_run["harness"]
    policy = stability_run["policy"]
    rolled_back = {int(c.get("from_chapter")) for c in harness.rollback_calls if c.get("from_chapter") is not None}
    assert policy.scripted_chapters() <= rolled_back, (
        f"expected scripted chapters {policy.scripted_chapters()} to roll back, got {rolled_back}"
    )


def test_run_stays_complete_despite_rollbacks(stability_run):
    """Resilience: rollbacks happen, yet every chapter still lands persisted."""
    harness = stability_run["harness"]
    assert harness.rollback_calls, "expected the hard schedule to trigger rollbacks"
    rows = stability_run["chapters"]
    assert len(rows) == _HARD_CHAPTERS
    assert all(r.content for r in rows)


# --- orchestration trace invariants ---------------------------------------


def test_trace_has_full_skeleton_and_recovery(stability_run):
    trace = stability_run["harness"].node_trace
    for node in ("init", "prewrite", "outline", "writer", "reviewer", "finalizer"):
        assert node in trace, f"missing node {node!r}"
    assert "revise" in trace
    assert "rollback_rerun" in trace


def test_trace_writer_reviewer_finalizer_ordering(stability_run):
    trace = stability_run["harness"].node_trace
    assert trace.index("writer") < trace.index("reviewer") < trace.index("finalizer")


def test_trace_covers_every_chapter(stability_run):
    """writer/reviewer/finalizer each fire at least once per persisted chapter."""
    trace = stability_run["harness"].node_trace
    n = _HARD_CHAPTERS
    assert trace.count("writer") >= n
    assert trace.count("reviewer") >= n
    assert trace.count("finalizer") >= n


# --- bounded work: no runaway loops ---------------------------------------


def test_work_is_bounded_no_runaway_loops(stability_run):
    """The #1 long-run failure mode is a retry/rollback loop that never ends.

    Every recovery path (review revise, review rollback_rerun, finalize quality
    rollback) is bounded per chapter, so total writer/reviewer work must stay
    within a generous multiple of the chapter count.
    """
    trace = stability_run["harness"].node_trace
    n = _HARD_CHAPTERS
    assert trace.count("writer") >= n
    assert trace.count("reviewer") >= n
    # Generous ceiling: initial + revises + rollback reruns + quality reruns.
    assert trace.count("writer") <= n * 8, trace.count("writer")
    assert trace.count("reviewer") <= n * 8, trace.count("reviewer")


# --- determinism: same script => same orchestration -----------------------


def _trace_multiset(schedule: dict[int, int], n: int) -> tuple[int, int]:
    """Run a small segment and return (node-trace signature hash, rollback count)."""
    import collections

    mp = pytest.MonkeyPatch()
    try:
        novel_id, version_id = seed_novel(title="P2 Determinism Novel")
        harness = install_offline_harness(
            mp, review_policy=ScriptedReviewPolicy(schedule=dict(schedule))
        )
        _run_segment(novel_id, version_id, n)
        signature = hash(tuple(sorted(collections.Counter(harness.node_trace).items())))
        return signature, len(harness.rollback_calls)
    finally:
        mp.undo()


def test_run_is_deterministic_across_repeats():
    """Same seed shape + schedule must yield the same node multiset and rollback
    count on repeat — long-run stability requires reproducible orchestration."""
    schedule = {2: 2}
    first = _trace_multiset(schedule, n=2)
    second = _trace_multiset(schedule, n=2)
    assert first == second, (first, second)
