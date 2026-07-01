"""Deterministic offline harness metrics report + CI gate (P4/P5).

Runs the real LangGraph generation pipeline against deterministic fakes (zero
external LLM/embedding calls) on a throwaway SQLite DB, then prints a JSON
metrics report. With ``--enforce-baseline`` it exits non-zero when the run
violates the committed stability baseline — a fast, hermetic CI gate that needs
no production database.

Usage:
  uv run python scripts/offline_harness_report.py [--chapters N] [--enforce-baseline]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# DB must be configured before importing any app module that binds the engine.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_TMP_DB = Path(tempfile.gettempdir()) / "ai_jinshu_offline_harness.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP_DB}")


def _prepare_db() -> None:
    from app import models as _models  # noqa: F401  (register ORM models)
    from app.core.database import Base, engine

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def _run(chapters: int, schedule: dict[int, int]) -> dict:
    import pytest

    from app.core.database import SessionLocal
    from app.models.novel import ChapterVersion
    from app.services.generation.langgraph_pipeline import (
        run_generation_pipeline_langgraph,
    )
    from sqlalchemy import select
    from tests.support.fake_llm import (
        ScriptedReviewPolicy,
        install_offline_harness,
        seed_novel,
    )
    from tests.support.metrics import summarize_run

    mp = pytest.MonkeyPatch()
    try:
        novel_id, version_id = seed_novel(title="Offline Harness Report")
        harness = install_offline_harness(
            mp, review_policy=ScriptedReviewPolicy(schedule=dict(schedule))
        )
        run_generation_pipeline_langgraph(
            novel_id=novel_id,
            novel_version_id=version_id,
            segment_target_chapters=chapters,
            segment_start_chapter=1,
            book_start_chapter=1,
            book_target_total_chapters=chapters,
            book_effective_end_chapter=chapters,
            volume_no=1,
            task_id=None,
            creation_task_id=None,
        )
        db = SessionLocal()
        try:
            rows = list(
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
        return summarize_run(
            node_trace=harness.node_trace,
            rollback_calls=harness.rollback_calls,
            chapters=rows,
        )
    finally:
        mp.undo()


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline harness metrics report")
    parser.add_argument("--chapters", type=int, default=6, help="segment length to run")
    parser.add_argument(
        "--enforce-baseline",
        action="store_true",
        help="Exit non-zero when the run violates the stability baseline",
    )
    args = parser.parse_args()

    _prepare_db()
    metrics = _run(args.chapters, schedule={3: 3})

    from tests.support.metrics import DEFAULT_BASELINE, check_baseline

    violations = check_baseline(metrics, DEFAULT_BASELINE) if args.enforce_baseline else []
    report = {"metrics": metrics}
    if args.enforce_baseline:
        report["baseline_gate"] = {"passed": not violations, "violations": violations}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if violations:
        sys.exit(2)


if __name__ == "__main__":
    main()
