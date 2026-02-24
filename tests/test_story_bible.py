"""Tests for phase-1 story bible persistence."""
from app.core.database import SessionLocal, resolve_novel
from app.services.memory.story_bible import StoryBibleStore, CheckpointStore, QualityReportStore


def test_story_bible_entity_and_fact(client):
    r = client.post("/api/novels", json={"title": "Bible Test", "target_language": "zh"})
    assert r.status_code == 200
    novel_public_id = r.json()["id"]
    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_public_id)
        assert novel is not None
        novel_id = int(novel.id)
    finally:
        db.close()

    store = StoryBibleStore()
    entity = store.upsert_entity(novel_id=novel_id, entity_type="character", name="林舟", status="alive")
    assert entity.id is not None
    assert entity.name == "林舟"

    fact = store.add_fact(
        novel_id=novel_id,
        entity_id=entity.id,
        fact_type="location",
        value_json={"value": "青云城"},
        chapter_from=1,
    )
    assert fact.id is not None
    assert fact.fact_type == "location"


def test_checkpoint_and_quality_report(client):
    r = client.post("/api/novels", json={"title": "Checkpoint Test", "target_language": "zh"})
    assert r.status_code == 200
    novel_public_id = r.json()["id"]
    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_public_id)
        assert novel is not None
        novel_id = int(novel.id)
    finally:
        db.close()

    cp_store = CheckpointStore()
    cp = cp_store.save_checkpoint(
        task_id="task-phase1-001",
        novel_id=novel_id,
        volume_no=1,
        chapter_num=3,
        node="writer",
        state_json={"k": "v"},
    )
    assert cp.id is not None
    assert cp.node == "writer"

    report_store = QualityReportStore()
    report = report_store.add_report(
        novel_id=novel_id,
        scope="chapter",
        scope_id="3",
        metrics_json={"coherence": 0.92},
        verdict="pass",
    )
    assert report.id is not None
    assert report.verdict == "pass"


def test_snapshot_query(client):
    r = client.post("/api/novels", json={"title": "Snapshot Test", "target_language": "zh"})
    assert r.status_code == 200
    novel_public_id = r.json()["id"]
    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_public_id)
        assert novel is not None
        novel_id = int(novel.id)
    finally:
        db.close()

    store = StoryBibleStore()
    store.save_snapshot(
        novel_id=novel_id,
        volume_no=1,
        chapter_end=30,
        snapshot_json={"avg_review_score": 0.73},
    )
    latest = store.get_latest_snapshot(novel_id=novel_id, volume_no=1)
    assert latest is not None
    assert latest.snapshot_json["avg_review_score"] == 0.73
