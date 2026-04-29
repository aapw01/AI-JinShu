"""Full-book fact ledger aggregation for long-form consistency review."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.novel import NovelMemory, StoryEntity, StoryEvent, StoryFact, StoryForeshadow, StoryRelation


def _json_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


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

    return {
        "novel_id": int(novel_id),
        "novel_version_id": int(novel_version_id) if novel_version_id is not None else None,
        "entities": dict(grouped_entities),
        "facts": fact_rows,
        "facts_by_entity": dict(facts_by_entity),
        "events": events,
        "foreshadows": foreshadows,
        "relations": relations,
        "character_memory": character_memory,
        "ledger_meta": {
            "entity_count": len(entities),
            "fact_count": len(facts),
            "event_count": len(events),
            "foreshadow_count": len(foreshadows),
            "relation_count": len(relations),
            "character_memory_count": len(character_memory),
        },
    }
