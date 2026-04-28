from __future__ import annotations

from app.core.database import SessionLocal
from app.models.creation_task import CreationTask
from app.services.generation.progress import persist_chapter_runtime_snapshot
from app.services.task_runtime.checkpoint_repo import get_resume_runtime_state


def test_persist_chapter_runtime_snapshot_merges_by_chapter():
    db = SessionLocal()
    try:
        task = CreationTask(
            user_uuid="user-1",
            task_type="generation",
            resource_type="novel",
            resource_id=1,
            status="running",
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = task.id
    finally:
        db.close()

    state = {
        "creation_task_id": task_id,
        "strategy": "web-novel",
        "context": {
            "context_selector_meta": {
                "included_block_ids": ["outline_contract"],
                "dropped_block_ids": ["knowledge_chunks"],
            },
            "context_sources": [{"source_type": "outline_contract", "included": True}],
            "budget_used": 100,
            "budget_total": 200,
        },
    }
    persist_chapter_runtime_snapshot(
        state,
        chapter_num=3,
        stage="writer",
        prompt_meta={
            "prompt_asset_id": "generation.chapter.writer",
            "prompt_version": "v2",
            "prompt_template": "next_chapter",
            "prompt_hash": "abc123",
            "provider": "openai",
            "model": "m",
        },
        diagnostics={"word_count_target": 3000},
    )

    db = SessionLocal()
    try:
        runtime_state = get_resume_runtime_state(db, creation_task_id=task_id)
    finally:
        db.close()

    snapshot = runtime_state["chapter_runtime_snapshots"]["3"]
    assert snapshot["stage"] == "writer"
    assert snapshot["prompt"]["prompt_asset_id"] == "generation.chapter.writer"
    assert snapshot["context"]["included_block_ids"] == ["outline_contract"]
    assert snapshot["context"]["dropped_block_ids"] == ["knowledge_chunks"]
    assert snapshot["diagnostics"]["word_count_target"] == 3000
