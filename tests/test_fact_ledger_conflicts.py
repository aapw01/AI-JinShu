"""Tests for fact-ledger conflict detection (pure + DB-backed)."""
from __future__ import annotations

import pytest

from app.core.database import SessionLocal
from app.models.novel import Novel, NovelMemory, StoryEntity, StoryFact
from app.services.generation.harness.fact_ledger import (
    build_fact_ledger,
    detect_fact_ledger_conflicts,
)

pytestmark = pytest.mark.offline


def test_detect_fact_value_conflict_on_overlapping_ranges():
    ledger = {
        "facts_by_entity": {
            "林秋": [
                {"fact_type": "location", "value_json": {"value": "旧宅"}, "chapter_from": 2, "chapter_to": None},
                {"fact_type": "location", "value_json": {"value": "京城"}, "chapter_from": 5, "chapter_to": None},
            ]
        },
        "entities": {},
        "character_memory": {},
    }
    conflicts = detect_fact_ledger_conflicts(ledger)
    assert len(conflicts) == 1
    assert conflicts[0]["type"] == "fact_value_conflict"
    assert conflicts[0]["entity"] == "林秋"
    assert conflicts[0]["fact_type"] == "location"


def test_no_conflict_when_ranges_disjoint():
    ledger = {
        "facts_by_entity": {
            "林秋": [
                {"fact_type": "location", "value_json": {"value": "旧宅"}, "chapter_from": 2, "chapter_to": 4},
                {"fact_type": "location", "value_json": {"value": "京城"}, "chapter_from": 5, "chapter_to": 8},
            ]
        },
        "entities": {},
        "character_memory": {},
    }
    assert detect_fact_ledger_conflicts(ledger) == []


def test_detect_status_mismatch_alive_vs_dead():
    ledger = {
        "facts_by_entity": {},
        "entities": {"character": [{"name": "陆沉", "status": "alive"}]},
        "character_memory": {"陆沉": {"status": "已死亡"}},
    }
    conflicts = detect_fact_ledger_conflicts(ledger)
    assert len(conflicts) == 1
    assert conflicts[0]["type"] == "status_mismatch"
    assert conflicts[0]["entity"] == "陆沉"


def test_build_fact_ledger_surfaces_conflict_from_db():
    db = SessionLocal()
    try:
        novel = Novel(title="冲突账本测试", target_language="zh")
        db.add(novel)
        db.commit()
        db.refresh(novel)
        novel_id = int(novel.id)

        entity = StoryEntity(
            novel_id=novel_id, entity_type="character", name="沈砚", status="alive"
        )
        db.add(entity)
        db.flush()
        db.add(
            StoryFact(
                novel_id=novel_id,
                entity_id=int(entity.id),
                fact_type="location",
                value_json={"value": "南境"},
                chapter_from=3,
            )
        )
        db.add(
            StoryFact(
                novel_id=novel_id,
                entity_id=int(entity.id),
                fact_type="location",
                value_json={"value": "北关"},
                chapter_from=7,
            )
        )
        db.add(
            NovelMemory(
                novel_id=novel_id,
                memory_type="character",
                key="沈砚",
                content={"status": "身故", "chapter_num": 9},
            )
        )
        db.commit()

        ledger = build_fact_ledger(db, novel_id=novel_id, novel_version_id=None)
    finally:
        db.close()

    assert ledger["ledger_meta"]["conflict_count"] >= 2
    kinds = {c["type"] for c in ledger["conflicts"]}
    assert "fact_value_conflict" in kinds
    assert "status_mismatch" in kinds
