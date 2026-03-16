from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.creation_task import CreationTask
from app.models.novel import ChapterOutline, Novel, NovelVersion
from app.services.generation.nodes.finalize import (
    _ensure_retry_write_allowed,
    _paragraph_fragmentation_metrics,
    _should_compact_paragraphs,
)
from app.services.generation.nodes.init_node import node_outline


def _create_novel_with_version(title: str) -> tuple[Novel, NovelVersion]:
    db = SessionLocal()
    try:
        novel = Novel(title=title, target_language="zh")
        db.add(novel)
        db.flush()
        version = NovelVersion(
            novel_id=novel.id,
            version_no=1,
            status="generating",
            is_default=1,
        )
        db.add(version)
        db.commit()
        db.refresh(novel)
        db.refresh(version)
        return novel, version
    finally:
        db.close()


def test_node_outline_restores_segment_plan_without_regenerating():
    novel, version = _create_novel_with_version("retry-plan-outline")
    db = SessionLocal()
    try:
        segment_plan = {
            "start_chapter": 1,
            "end_chapter": 30,
            "volume_no": 1,
            "plan_kind": "normal",
            "outlines": [
                {
                    "chapter_num": chapter_num,
                    "title": f"第{chapter_num}章",
                    "outline": f"原计划-{chapter_num}",
                    "purpose": "推进主线",
                }
                for chapter_num in range(1, 31)
            ],
        }
        creation_task = CreationTask(
            user_uuid="u-outline",
            task_type="generation",
            resource_type="novel",
            resource_id=novel.id,
            status="running",
            payload_json={
                "novel_id": novel.id,
                "novel_version_id": version.id,
                "start_chapter": 1,
                "num_chapters": 30,
                "book_start_chapter": 1,
                "book_target_total_chapters": 200,
            },
            resume_cursor_json={
                "unit_type": "chapter",
                "partition": None,
                "last_completed": 24,
                "next": 25,
                "runtime_state": {
                    "mode": "segment_running",
                    "volume_no": 1,
                    "segment_start_chapter": 1,
                    "segment_end_chapter": 30,
                    "next_chapter": 25,
                    "book_effective_end_chapter": 200,
                    "book_target_total_chapters": 200,
                    "retry_resume_chapter": 25,
                    "segment_plan": segment_plan,
                },
            },
        )
        db.add(creation_task)
        db.flush()
        for chapter_num in range(1, 25):
            db.add(
                ChapterOutline(
                    novel_id=novel.id,
                    novel_version_id=version.id,
                    chapter_num=chapter_num,
                    title=f"第{chapter_num}章",
                    outline=f"已有-{chapter_num}",
                    metadata_={"purpose": "推进主线"},
                )
            )
        db.commit()
        task_id = int(creation_task.id)
    finally:
        db.close()

    class _OutlinerShouldNotRun:
        def run_full_book(self, *args, **kwargs):  # pragma: no cover - assertion path
            raise AssertionError("ordinary retry should not regenerate outlines")

    out = node_outline(
        {
            "novel_id": novel.id,
            "novel_version_id": version.id,
            "creation_task_id": task_id,
            "strategy": "web-novel",
            "target_language": "zh",
            "volume_no": 1,
            "start_chapter": 1,
            "segment_start_chapter": 1,
            "end_chapter": 30,
            "segment_end_chapter": 30,
            "num_chapters": 30,
            "book_effective_end_chapter": 200,
            "book_start_chapter": 1,
            "book_target_total_chapters": 200,
            "current_chapter": 25,
            "retry_resume_chapter": 25,
            "progress_callback": None,
            "outliner": _OutlinerShouldNotRun(),
            "prewrite": {"specification": {}},
            "segment_plan": segment_plan,
        }
    )

    chapter_nums = [int(item.get("chapter_num") or 0) for item in out["full_outlines"]]
    assert chapter_nums[:30] == list(range(1, 31))
    assert out["retry_resume_chapter"] == 25
    assert out["segment_plan"]["start_chapter"] == 1
    assert out["segment_plan"]["end_chapter"] == 30

    db = SessionLocal()
    try:
        restored = db.execute(
            select(ChapterOutline).where(
                ChapterOutline.novel_id == novel.id,
                ChapterOutline.novel_version_id == version.id,
                ChapterOutline.chapter_num == 30,
            )
        ).scalar_one_or_none()
        assert restored is not None
        assert restored.outline == "原计划-30"
    finally:
        db.close()


def test_retry_write_guard_blocks_overwrite_before_retry_floor():
    state = {
        "retry_resume_chapter": 25,
        "segment_plan": {"plan_kind": "normal"},
    }
    try:
        _ensure_retry_write_allowed(state, 24)
        assert False, "expected overwrite guard to block chapter 24"
    except RuntimeError as exc:
        assert "retry overwrite blocked" in str(exc)


def test_retry_write_guard_allows_tail_rewrite_before_retry_floor():
    state = {
        "retry_resume_chapter": 25,
        "segment_plan": {"plan_kind": "tail_rewrite"},
    }
    _ensure_retry_write_allowed(state, 24)


def test_fragmentation_metrics_detect_excessive_short_paragraphs():
    content = "\n\n".join(
        [
            "她怔住了。",
            "心口一沉。",
            "风从窗外灌进来。",
            "陆之眠没有解释，只是抬眼看她。",
            "“走。”",
            "他只说了这一个字。",
            "她还是跟了上去。",
            "楼道尽头的灯忽明忽暗，空气里全是雨前潮气。",
            "她忽然意识到，事情比她以为的更糟。",
            "更糟。",
            "真的更糟。",
            "可她已经没有退路。",
            "她必须继续往前。",
            "必须。",
            "门开了。",
            "黑暗一下子扑了出来。",
            "她屏住呼吸。",
            "心跳声重得几乎盖过脚步声。",
        ]
    )
    metrics = _paragraph_fragmentation_metrics(content)
    assert metrics["paragraph_count"] == 18.0
    assert metrics["short_paragraph_ratio"] > 0.3
    assert _should_compact_paragraphs(metrics) is True
