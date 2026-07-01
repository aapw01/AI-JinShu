"""Full-book fact ledger aggregation for long-form consistency review."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.novel import NovelMemory, StoryEntity, StoryEvent, StoryFact, StoryForeshadow, StoryRelation

_DEAD_TOKENS = ("dead", "deceased", "死", "亡", "殁", "身故")
_ALIVE_TOKENS = ("alive", "living", "存活", "活着", "在世")


def _json_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _life_state(status: Any) -> str | None:
    """Classify a status string as ``dead`` / ``alive`` / ``None`` (unknown)."""
    text = str(status or "").lower()
    if not text:
        return None
    if any(tok in text for tok in _DEAD_TOKENS):
        return "dead"
    if any(tok in text for tok in _ALIVE_TOKENS):
        return "alive"
    return None


def _value_signature(value_json: Any) -> str:
    """Stable signature for a fact value, order-independent for dicts."""
    try:
        return json.dumps(value_json, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value_json)


def _ranges_overlap(a_from: int, a_to: int | None, b_from: int, b_to: int | None) -> bool:
    """Inclusive overlap where ``None`` upper bound means open-ended (+inf)."""
    a_hi = a_to if a_to is not None else float("inf")
    b_hi = b_to if b_to is not None else float("inf")
    return a_from <= b_hi and b_from <= a_hi


def detect_fact_ledger_conflicts(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    """Detect likely contradictions in an aggregated ledger (pure, offline).

    Two signals:
    - ``fact_value_conflict``: same entity + fact_type has differing values whose
      chapter ranges overlap (e.g. two simultaneous "current location"s).
    - ``status_mismatch``: an entity's ``status`` and its character-memory status
      classify to opposite life states (alive vs dead).
    """
    conflicts: list[dict[str, Any]] = []

    facts_by_entity: dict[str, list[dict[str, Any]]] = ledger.get("facts_by_entity") or {}
    for entity_name, rows in facts_by_entity.items():
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row.get("fact_type") or "")].append(row)
        for fact_type, group in grouped.items():
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i], group[j]
                    if _value_signature(a.get("value_json")) == _value_signature(
                        b.get("value_json")
                    ):
                        continue
                    if _ranges_overlap(
                        int(a.get("chapter_from") or 0),
                        a.get("chapter_to"),
                        int(b.get("chapter_from") or 0),
                        b.get("chapter_to"),
                    ):
                        conflicts.append(
                            {
                                "type": "fact_value_conflict",
                                "entity": entity_name,
                                "fact_type": fact_type,
                                "left": {
                                    "value": a.get("value_json"),
                                    "chapter_from": a.get("chapter_from"),
                                    "chapter_to": a.get("chapter_to"),
                                },
                                "right": {
                                    "value": b.get("value_json"),
                                    "chapter_from": b.get("chapter_from"),
                                    "chapter_to": b.get("chapter_to"),
                                },
                            }
                        )

    character_memory: dict[str, dict[str, Any]] = ledger.get("character_memory") or {}
    for _entity_type, rows in (ledger.get("entities") or {}).items():
        for entity in rows:
            name = str(entity.get("name") or "")
            entity_state = _life_state(entity.get("status"))
            mem_state = _life_state((character_memory.get(name) or {}).get("status"))
            if entity_state and mem_state and entity_state != mem_state:
                conflicts.append(
                    {
                        "type": "status_mismatch",
                        "entity": name,
                        "entity_status": entity.get("status"),
                        "memory_status": (character_memory.get(name) or {}).get("status"),
                    }
                )

    return conflicts


def _json_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _version_filter(stmt: Any, model: Any, novel_version_id: int | None) -> Any:
    if novel_version_id is None:
        return stmt
    return stmt.where(model.novel_version_id == int(novel_version_id))


def _entity_row(entity: StoryEntity) -> dict[str, Any]:
    return {
        "id": int(entity.id),
        "entity_type": entity.entity_type,
        "name": entity.name,
        "status": entity.status,
        "summary": entity.summary,
        "metadata": _json_mapping(entity.metadata_),
        "revision": int(entity.revision or 0),
    }


def _fact_row(fact: StoryFact, entity_name: str) -> dict[str, Any]:
    return {
        "id": int(fact.id),
        "entity_id": int(fact.entity_id),
        "entity_name": entity_name,
        "fact_type": fact.fact_type,
        "value_json": _json_mapping(fact.value_json),
        "chapter_from": int(fact.chapter_from),
        "chapter_to": int(fact.chapter_to) if fact.chapter_to is not None else None,
        "revision": int(fact.revision or 0),
    }


def _event_row(event: StoryEvent) -> dict[str, Any]:
    return {
        "id": int(event.id),
        "event_id": event.event_id,
        "chapter_num": int(event.chapter_num),
        "title": event.title,
        "event_type": event.event_type,
        "actors": _json_list(event.actors),
        "causes": _json_list(event.causes),
        "effects": _json_list(event.effects),
        "payload": _json_mapping(event.payload),
    }


def _foreshadow_row(foreshadow: StoryForeshadow) -> dict[str, Any]:
    return {
        "id": int(foreshadow.id),
        "foreshadow_id": foreshadow.foreshadow_id,
        "title": foreshadow.title,
        "planted_chapter": int(foreshadow.planted_chapter),
        "resolved_chapter": int(foreshadow.resolved_chapter) if foreshadow.resolved_chapter is not None else None,
        "state": foreshadow.state,
        "payload": _json_mapping(foreshadow.payload),
    }


def _relation_row(relation: StoryRelation) -> dict[str, Any]:
    return {
        "id": int(relation.id),
        "source": relation.source,
        "target": relation.target,
        "relation_type": relation.relation_type,
        "description": relation.description,
        "sentiment": relation.sentiment,
        "chapter_num": int(relation.chapter_num or 0),
    }


def build_fact_ledger(db: Session, *, novel_id: int, novel_version_id: int | None = None) -> dict[str, Any]:
    """Aggregate structured story state into a single consistency ledger."""
    entity_stmt = select(StoryEntity).where(StoryEntity.novel_id == int(novel_id)).order_by(
        StoryEntity.entity_type,
        StoryEntity.name,
        StoryEntity.id,
    )
    entity_stmt = _version_filter(entity_stmt, StoryEntity, novel_version_id)
    entities = list(db.execute(entity_stmt).scalars().all())
    entity_names_by_id = {int(entity.id): str(entity.name) for entity in entities}

    grouped_entities: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for entity in entities:
        grouped_entities[str(entity.entity_type)].append(_entity_row(entity))

    fact_stmt = select(StoryFact).where(StoryFact.novel_id == int(novel_id)).order_by(
        StoryFact.chapter_from,
        StoryFact.fact_type,
        StoryFact.id,
    )
    fact_stmt = _version_filter(fact_stmt, StoryFact, novel_version_id)
    facts = list(db.execute(fact_stmt).scalars().all())
    fact_rows: list[dict[str, Any]] = []
    facts_by_entity: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        entity_name = entity_names_by_id.get(int(fact.entity_id), str(fact.entity_id))
        row = _fact_row(fact, entity_name)
        fact_rows.append(row)
        facts_by_entity[entity_name].append(row)

    event_stmt = select(StoryEvent).where(StoryEvent.novel_id == int(novel_id)).order_by(
        StoryEvent.chapter_num,
        StoryEvent.event_id,
        StoryEvent.id,
    )
    event_stmt = _version_filter(event_stmt, StoryEvent, novel_version_id)
    events = [_event_row(event) for event in db.execute(event_stmt).scalars().all()]

    foreshadow_stmt = select(StoryForeshadow).where(StoryForeshadow.novel_id == int(novel_id)).order_by(
        StoryForeshadow.planted_chapter,
        StoryForeshadow.foreshadow_id,
        StoryForeshadow.id,
    )
    foreshadow_stmt = _version_filter(foreshadow_stmt, StoryForeshadow, novel_version_id)
    foreshadows = [_foreshadow_row(item) for item in db.execute(foreshadow_stmt).scalars().all()]

    relation_stmt = select(StoryRelation).where(StoryRelation.novel_id == int(novel_id)).order_by(
        StoryRelation.chapter_num,
        StoryRelation.source,
        StoryRelation.target,
        StoryRelation.id,
    )
    relation_stmt = _version_filter(relation_stmt, StoryRelation, novel_version_id)
    relations = [_relation_row(relation) for relation in db.execute(relation_stmt).scalars().all()]

    memory_stmt = select(NovelMemory).where(
        NovelMemory.novel_id == int(novel_id),
        NovelMemory.memory_type == "character",
    )
    memory_stmt = _version_filter(memory_stmt, NovelMemory, novel_version_id)
    memories = db.execute(memory_stmt.order_by(NovelMemory.key, NovelMemory.id)).scalars().all()
    character_memory = {
        str(memory.key): _json_mapping(memory.content)
        for memory in memories
        if memory.key
    }

    ledger = {
        "novel_id": int(novel_id),
        "novel_version_id": int(novel_version_id) if novel_version_id is not None else None,
        "entities": dict(grouped_entities),
        "facts": fact_rows,
        "facts_by_entity": dict(facts_by_entity),
        "events": events,
        "foreshadows": foreshadows,
        "relations": relations,
        "character_memory": character_memory,
    }
    conflicts = detect_fact_ledger_conflicts(ledger)
    ledger["conflicts"] = conflicts
    ledger["ledger_meta"] = {
        "entity_count": len(entities),
        "fact_count": len(facts),
        "event_count": len(events),
        "foreshadow_count": len(foreshadows),
        "relation_count": len(relations),
        "character_memory_count": len(character_memory),
        "conflict_count": len(conflicts),
    }
    return ledger
