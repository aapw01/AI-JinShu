from __future__ import annotations

import runpy
from pathlib import Path

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.novel import ChapterVersion, GenerationCheckpoint, Novel, NovelVersion


def _load_script_module() -> dict:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "evaluate_generation_metrics.py"
    return runpy.run_path(str(script_path), run_name="evaluate_generation_metrics_test")


def test_evaluate_generation_metrics_uses_default_version_tail_chapters() -> None:
    module = _load_script_module()
    evaluate_one = module["_evaluate_one"]
    is_evaluable_novel = module["_is_evaluable_novel"]

    db = SessionLocal()
    try:
        novel = Novel(title="Metrics Script Novel", status="completed", target_language="zh")
        db.add(novel)
        db.commit()
        db.refresh(novel)

        default_version = NovelVersion(novel_id=novel.id, version_no=1, is_default=1, status="completed")
        alt_version = NovelVersion(novel_id=novel.id, version_no=2, is_default=0, status="completed")
        db.add_all([default_version, alt_version])
        db.commit()
        db.refresh(default_version)
        db.refresh(alt_version)

        db.add_all(
                [
                    ChapterVersion(
                        novel_version_id=default_version.id,
                        chapter_num=8,
                        title="第8章",
                        content="真相逼近，关键矛盾已经摊开。",
                        status="completed",
                    ),
                    ChapterVersion(
                        novel_version_id=default_version.id,
                        chapter_num=9,
                        title="第9章",
                    content="主角进入最终决战前夜，仍有明显悬念。",
                    status="completed",
                ),
                ChapterVersion(
                    novel_version_id=default_version.id,
                    chapter_num=10,
                    title="第10章",
                    content="大战结束，但真正幕后黑手仍未揭晓，故事突然停止。",
                    status="completed",
                ),
                ChapterVersion(
                    novel_version_id=alt_version.id,
                    chapter_num=10,
                    title="第10章-改写版",
                    content="所有冲突都已收束，故事圆满落幕。",
                    status="completed",
                ),
                GenerationCheckpoint(
                    novel_id=novel.id,
                    task_id="metrics-script-task",
                    volume_no=1,
                    node="closure_gate",
                    chapter_num=10,
                    state_json={"unresolved_count": 1, "action": "continue"},
                ),
                GenerationCheckpoint(
                    novel_id=novel.id,
                    task_id="metrics-script-task",
                    volume_no=1,
                    node="chapter_done",
                    chapter_num=10,
                    state_json={"progress_signal": 0.62},
                ),
            ]
        )
        db.commit()

        assert is_evaluable_novel(db, novel) is True
        metric = evaluate_one(db, novel)

        assert metric.unresolved_mainline is True
        assert metric.abrupt_score > 0
        assert metric.abrupt_risk is True
    finally:
        db.query(GenerationCheckpoint).where(GenerationCheckpoint.task_id == "metrics-script-task").delete(
            synchronize_session=False
        )
        versions = db.execute(select(NovelVersion.id).where(NovelVersion.novel_id == novel.id)).scalars().all()
        if versions:
            db.query(ChapterVersion).where(ChapterVersion.novel_version_id.in_(versions)).delete(synchronize_session=False)
        db.query(NovelVersion).where(NovelVersion.novel_id == novel.id).delete(synchronize_session=False)
        db.query(Novel).where(Novel.id == novel.id).delete(synchronize_session=False)
        db.commit()
        db.close()


def test_evaluate_generation_metrics_skips_novels_without_progress_signal() -> None:
    module = _load_script_module()
    is_evaluable_novel = module["_is_evaluable_novel"]

    db = SessionLocal()
    try:
        novel = Novel(title="Metrics Skip Novel", status="completed", target_language="zh")
        db.add(novel)
        db.commit()
        db.refresh(novel)

        version = NovelVersion(novel_id=novel.id, version_no=1, is_default=1, status="completed")
        db.add(version)
        db.commit()
        db.refresh(version)

        db.add_all(
            [
                ChapterVersion(novel_version_id=version.id, chapter_num=1, title="第1章", content="正文1", status="completed"),
                ChapterVersion(novel_version_id=version.id, chapter_num=2, title="第2章", content="正文2", status="completed"),
                ChapterVersion(novel_version_id=version.id, chapter_num=3, title="第3章", content="正文3", status="completed"),
                GenerationCheckpoint(
                    novel_id=novel.id,
                    task_id="metrics-skip-task",
                    volume_no=1,
                    node="closure_gate",
                    chapter_num=3,
                    state_json={"unresolved_count": 0, "action": "continue"},
                ),
                GenerationCheckpoint(
                    novel_id=novel.id,
                    task_id="metrics-skip-task",
                    volume_no=1,
                    node="chapter_done",
                    chapter_num=3,
                    state_json={"other_key": 1},
                ),
            ]
        )
        db.commit()

        assert is_evaluable_novel(db, novel) is False
    finally:
        db.query(GenerationCheckpoint).where(
            GenerationCheckpoint.task_id.in_(("metrics-skip-task",))
        ).delete(synchronize_session=False)
        db.query(ChapterVersion).where(ChapterVersion.novel_version_id == version.id).delete(synchronize_session=False)
        db.query(NovelVersion).where(NovelVersion.id == version.id).delete(synchronize_session=False)
        db.query(Novel).where(Novel.id == novel.id).delete(synchronize_session=False)
        db.commit()
        db.close()
