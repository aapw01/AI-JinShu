from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.novel import ChapterOutline, Novel, NovelVersion
from app.services.generation.nodes.closure import node_bridge_chapter, node_closure_gate


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


class _DummyOutliner:
    def __init__(self):
        self.calls: list[tuple[int, dict]] = []

    def run(self, novel_id, chapter_num, plan, language="zh", provider=None, model=None):
        self.calls.append((int(chapter_num), dict(plan)))
        return {
            "title": f"第{chapter_num}章 桥接收束",
            "outline": "补完收官前的最后障碍",
            "purpose": "承接上一章并推进收束",
            "summary": "桥接章摘要",
            "hook": "新的危机",
        }


class _DummyCheckpointStore:
    def save_checkpoint(self, **kwargs):
        return None


def test_closure_gate_bridge_persists_outline_before_runtime_state(monkeypatch):
    monkeypatch.setattr("app.services.generation.nodes.closure.get_model_for_stage", lambda *_: ("openai", "mock"))
    monkeypatch.setattr(
        "app.services.generation.nodes.closure.build_closure_state",
        lambda _state: {
            "phase_mode": "closing",
            "action": "bridge_chapter",
            "closure_score": 0.61,
            "must_close_coverage": 0.2,
            "closure_threshold": 0.95,
            "unresolved_count": 2,
            "bridge_budget_left": 1,
            "bridge_budget_total": 2,
            "min_total_chapters": 15,
            "max_total_chapters": 17,
            "must_close_items": [],
            "tail_rewrite_attempts": 0,
            "reason_codes": ["unresolved_pending"],
            "confidence": 0.88,
        },
    )
    novel, version = _create_novel_with_version("bridge-outline-test")
    outliner = _DummyOutliner()
    state = {
        "novel_id": novel.id,
        "novel_version_id": version.id,
        "task_id": None,
        "creation_task_id": None,
        "current_chapter": 16,
        "start_chapter": 1,
        "end_chapter": 15,
        "num_chapters": 15,
        "target_chapters": 15,
        "strategy": "web-novel",
        "target_language": "zh",
        "outliner": outliner,
        "prewrite": {"specification": {"theme": "先婚后爱"}},
        "full_outlines": [
            {"chapter_num": i, "title": f"第{i}章", "outline": f"已有提纲{i}"}
            for i in range(1, 16)
        ],
        "closure_state": {},
        "decision_state": {"closure": {}},
        "volume_plan": {"quality_focus": ["收束主线"]},
        "progress_callback": None,
        "checkpoint_store": _DummyCheckpointStore(),
    }

    out = node_closure_gate(state)

    assert len(outliner.calls) == 1
    assert out["outline"]["chapter_num"] == 16
    assert out["outline"]["title"] == "第16章 桥接收束"
    assert len(out["full_outlines"]) == 16
    assert out["full_outlines"][-1]["chapter_num"] == 16
    assert out["end_chapter"] == 16
    assert out["num_chapters"] == 16

    db = SessionLocal()
    try:
        row = db.execute(
            select(ChapterOutline).where(
                ChapterOutline.novel_id == novel.id,
                ChapterOutline.novel_version_id == version.id,
                ChapterOutline.chapter_num == 16,
            )
        ).scalar_one_or_none()
        assert row is not None
        assert row.title == "第16章 桥接收束"
        assert row.outline == "补完收官前的最后障碍"
        assert row.metadata_["purpose"] == "承接上一章并推进收束"
        assert row.metadata_["summary"] == "桥接章摘要"
    finally:
        db.close()


def test_bridge_chapter_node_preserves_existing_outline_state():
    out = node_bridge_chapter(
        {
            "current_chapter": 16,
            "start_chapter": 1,
            "end_chapter": 16,
            "num_chapters": 16,
            "closure_state": {"action": "bridge_chapter"},
            "decision_state": {"closure": {"action": "bridge_chapter"}},
            "progress_callback": None,
            "full_outlines": [{"chapter_num": 16, "title": "第16章 桥接收束"}],
            "outline": {"chapter_num": 16, "title": "第16章 桥接收束"},
            "task_id": None,
            "creation_task_id": None,
        }
    )
    assert "outline" not in out
    assert "full_outlines" not in out
    assert out["closure_state"]["action"] == "continue"
