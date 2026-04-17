"""Story bible data access helpers for long-form generation."""
from __future__ import annotations

from typing import Optional
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.novel import (
    StoryEntity,
    StoryFact,
    StoryEvent,
    StoryForeshadow,
    StorySnapshot,
    GenerationCheckpoint,
    QualityReport,
    NovelMemory,
    StoryRelation,
)


class StoryBibleStore:
    """Data access layer for structured long-form memory."""

    def upsert_entity(
        self,
        novel_id: int,
        entity_type: str,
        name: str,
        status: str | None = None,
        summary: str | None = None,
        metadata: dict | None = None,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> StoryEntity:
        """插入或更新entity。"""
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(StoryEntity).where(StoryEntity.novel_id == novel_id)
            if novel_version_id is not None:
                stmt = stmt.where(StoryEntity.novel_version_id == novel_version_id)
            stmt = stmt.where(StoryEntity.entity_type == entity_type, StoryEntity.name == name)
            row = db.execute(stmt).scalar_one_or_none()
            if row:
                row.status = status if status is not None else row.status
                row.summary = summary if summary is not None else row.summary
                row.metadata_ = {**(row.metadata_ or {}), **(metadata or {})}
                row.revision = (row.revision or 1) + 1
            else:
                try:
                    with db.begin_nested():
                        row = StoryEntity(
                            novel_id=novel_id,
                            novel_version_id=novel_version_id,
                            entity_type=entity_type,
                            name=name,
                            status=status,
                            summary=summary,
                            metadata_=metadata or {},
                            revision=1,
                        )
                        db.add(row)
                        db.flush()
                except IntegrityError:
                    row = db.execute(stmt).scalar_one_or_none()
                    if row is None:
                        raise
                    row.status = status if status is not None else row.status
                    row.summary = summary if summary is not None else row.summary
                    row.metadata_ = {**(row.metadata_ or {}), **(metadata or {})}
                    row.revision = (row.revision or 1) + 1
            if should_close:
                db.commit()
            else:
                db.flush()
            db.refresh(row)
            return row
        except Exception:
            if should_close:
                db.rollback()
            raise
        finally:
            if should_close:
                db.close()

    def add_fact(
        self,
        novel_id: int,
        entity_id: int,
        fact_type: str,
        value_json: dict,
        chapter_from: int,
        chapter_to: int | None = None,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> StoryFact:
        """执行 add fact 相关辅助逻辑。"""
        should_close = db is None
        db = db or SessionLocal()
        try:
            row = StoryFact(
                novel_id=novel_id,
                novel_version_id=novel_version_id,
                entity_id=entity_id,
                fact_type=fact_type,
                value_json=value_json,
                chapter_from=chapter_from,
                chapter_to=chapter_to,
                revision=1,
            )
            db.add(row)
            if should_close:
                db.commit()
            else:
                db.flush()
            db.refresh(row)
            return row
        except Exception:
            if should_close:
                db.rollback()
            raise
        finally:
            if should_close:
                db.close()

    def add_event(
        self,
        novel_id: int,
        event_id: str,
        chapter_num: int,
        title: str | None = None,
        event_type: str | None = None,
        actors: list | None = None,
        causes: list | None = None,
        effects: list | None = None,
        payload: dict | None = None,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> StoryEvent:
        """执行 add event 相关辅助逻辑。"""
        should_close = db is None
        db = db or SessionLocal()
        try:
            row = StoryEvent(
                novel_id=novel_id,
                novel_version_id=novel_version_id,
                event_id=event_id,
                chapter_num=chapter_num,
                title=title,
                event_type=event_type,
                actors=actors or [],
                causes=causes or [],
                effects=effects or [],
                payload=payload or {},
            )
            db.add(row)
            if should_close:
                db.commit()
            else:
                db.flush()
            db.refresh(row)
            return row
        except Exception:
            if should_close:
                db.rollback()
            raise
        finally:
            if should_close:
                db.close()

    def upsert_foreshadow(
        self,
        novel_id: int,
        foreshadow_id: str,
        planted_chapter: int,
        title: str | None = None,
        state: str = "planted",
        resolved_chapter: int | None = None,
        payload: dict | None = None,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> StoryForeshadow:
        """插入或更新foreshadow。"""
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(StoryForeshadow).where(StoryForeshadow.novel_id == novel_id)
            if novel_version_id is not None:
                stmt = stmt.where(StoryForeshadow.novel_version_id == novel_version_id)
            stmt = stmt.where(StoryForeshadow.foreshadow_id == foreshadow_id)
            row = db.execute(stmt).scalar_one_or_none()
            if row:
                row.title = title if title is not None else row.title
                row.state = state
                row.resolved_chapter = resolved_chapter
                row.payload = {**(row.payload or {}), **(payload or {})}
            else:
                try:
                    with db.begin_nested():
                        row = StoryForeshadow(
                            novel_id=novel_id,
                            novel_version_id=novel_version_id,
                            foreshadow_id=foreshadow_id,
                            title=title,
                            planted_chapter=planted_chapter,
                            resolved_chapter=resolved_chapter,
                            state=state,
                            payload=payload or {},
                        )
                        db.add(row)
                        db.flush()
                except IntegrityError:
                    row = db.execute(stmt).scalar_one_or_none()
                    if row is None:
                        raise
                    row.title = title if title is not None else row.title
                    row.state = state
                    row.resolved_chapter = resolved_chapter
                    row.payload = {**(row.payload or {}), **(payload or {})}
            if should_close:
                db.commit()
            else:
                db.flush()
            db.refresh(row)
            return row
        except Exception:
            if should_close:
                db.rollback()
            raise
        finally:
            if should_close:
                db.close()

    def resolve_foreshadow(
        self,
        novel_id: int,
        foreshadow_id: str,
        chapter_resolved: int,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> StoryForeshadow | None:
        """Mark a foreshadow as resolved in the given chapter."""
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(StoryForeshadow).where(StoryForeshadow.novel_id == novel_id)
            if novel_version_id is not None:
                stmt = stmt.where(StoryForeshadow.novel_version_id == novel_version_id)
            stmt = stmt.where(StoryForeshadow.foreshadow_id == foreshadow_id)
            row = db.execute(stmt).scalar_one_or_none()
            if row is None:
                return None
            row.state = "resolved"
            row.resolved_chapter = chapter_resolved
            if should_close:
                db.commit()
            else:
                db.flush()
            db.refresh(row)
            return row
        except Exception as exc:
            import logging as _logging
            _logging.getLogger("app.services.memory").warning(
                "resolve_foreshadow failed novel_id=%s foreshadow_id=%s error=%s", novel_id, foreshadow_id, exc
            )
            if should_close:
                try:
                    db.rollback()
                except Exception:
                    pass
            return None
        finally:
            if should_close:
                db.close()

    def save_snapshot(
        self,
        novel_id: int,
        volume_no: int,
        chapter_end: int,
        snapshot_json: dict,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> StorySnapshot:
        """保存snapshot。"""
        should_close = db is None
        db = db or SessionLocal()
        try:
            row = StorySnapshot(
                novel_id=novel_id,
                novel_version_id=novel_version_id,
                volume_no=volume_no,
                chapter_end=chapter_end,
                snapshot_json=snapshot_json,
            )
            db.add(row)
            if should_close:
                db.commit()
            else:
                db.flush()
            db.refresh(row)
            return row
        except Exception:
            if should_close:
                db.rollback()
            raise
        finally:
            if should_close:
                db.close()

    def get_latest_snapshot(
        self,
        novel_id: int,
        volume_no: int,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> StorySnapshot | None:
        """Return the newest snapshot for a volume."""
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(StorySnapshot).where(StorySnapshot.novel_id == novel_id)
            if novel_version_id is not None:
                stmt = stmt.where(StorySnapshot.novel_version_id == novel_version_id)
            stmt = stmt.where(StorySnapshot.volume_no == volume_no).order_by(StorySnapshot.id.desc())
            return db.execute(stmt).scalars().first()
        finally:
            if should_close:
                db.close()

    def get_chapter_constraints(
        self,
        novel_id: int,
        chapter_num: int,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> dict:
        """Return hard constraints for a target chapter."""
        should_close = db is None
        db = db or SessionLocal()
        try:
            dead_stmt = select(StoryEntity.name).where(StoryEntity.novel_id == novel_id)
            if novel_version_id is not None:
                dead_stmt = dead_stmt.where(StoryEntity.novel_version_id == novel_version_id)
            dead_stmt = dead_stmt.where(StoryEntity.entity_type == "character", StoryEntity.status == "dead")
            dead_names = [r[0] for r in db.execute(dead_stmt).all() if r and r[0]]

            unresolved_stmt = select(StoryForeshadow).where(StoryForeshadow.novel_id == novel_id)
            if novel_version_id is not None:
                unresolved_stmt = unresolved_stmt.where(StoryForeshadow.novel_version_id == novel_version_id)
            unresolved_stmt = unresolved_stmt.where(
                StoryForeshadow.state == "planted",
                StoryForeshadow.planted_chapter < chapter_num,
            )
            unresolved = db.execute(unresolved_stmt).scalars().all()
            memory_stmt = select(NovelMemory).where(NovelMemory.novel_id == novel_id)
            if novel_version_id is not None:
                memory_stmt = memory_stmt.where(NovelMemory.novel_version_id == novel_version_id)
            memory_stmt = memory_stmt.where(NovelMemory.memory_type == "character")
            memory_rows = db.execute(memory_stmt).scalars().all()

            entity_hard_constraints: list[dict] = []
            for row in memory_rows:
                content = row.content if isinstance(row.content, dict) else {}
                name = str(row.key or content.get("name") or "").strip()
                if not name:
                    continue
                source_chapter = int(content.get("chapter_num") or 0)
                status = str(content.get("status") or "").lower()
                if status == "dead":
                    entity_hard_constraints.append(
                        {
                            "entity": name,
                            "constraint_type": "forbidden_presence",
                            "description": f"角色{name}已死亡，不应再次以正常出场参与行动。",
                            "forbidden_patterns": [],
                            "confidence": 0.95,
                            "source_chapter": source_chapter,
                        }
                    )

                # Generic capability constraints from extracted state.
                raw_limits = content.get("limitations") or content.get("forbidden_actions") or []
                limitations: list[str] = [str(x).strip() for x in raw_limits if str(x).strip()] if isinstance(raw_limits, list) else []
                lost_items = content.get("lost_items") or []
                injuries = content.get("injuries") or []
                limb_loss = " ".join([str(x) for x in (lost_items if isinstance(lost_items, list) else [])] + [str(x) for x in (injuries if isinstance(injuries, list) else [])])
                if any(k in limb_loss for k in ["断臂", "断手", "失去右手", "失去左手", "失去双手", "断右臂", "断左臂"]):
                    limitations.append("双手持")
                    limitations.append("双臂发力")
                if bool(content.get("can_use_both_hands") is False):
                    limitations.append("双手持")
                    limitations.append("双手结印")

                dedup_limits: list[str] = []
                for x in limitations:
                    if x and x not in dedup_limits:
                        dedup_limits.append(x)
                if dedup_limits:
                    entity_hard_constraints.append(
                        {
                            "entity": name,
                            "constraint_type": "forbidden_action_pattern",
                            "description": f"角色{name}存在能力限制，不应出现与限制冲突的动作描写。",
                            "forbidden_patterns": dedup_limits[:8],
                            "confidence": 0.8,
                            "source_chapter": source_chapter,
                        }
                    )

            return {
                "forbidden_characters": dead_names,
                "unresolved_foreshadows": [
                    {
                        "foreshadow_id": f.foreshadow_id,
                        "title": f.title,
                        "planted_chapter": f.planted_chapter,
                    }
                    for f in unresolved
                ],
                "entity_hard_constraints": entity_hard_constraints,
                "constraint_registry_meta": {
                    "chapter_num": chapter_num,
                    "character_memory_count": len(memory_rows),
                    "hard_constraint_count": len(entity_hard_constraints),
                },
            }
        finally:
            if should_close:
                db.close()

    def list_entities(
        self,
        novel_id: int,
        entity_type: str | None = None,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> list[StoryEntity]:
        """List entities for a novel, optionally filtered by type."""
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(StoryEntity).where(StoryEntity.novel_id == novel_id)
            if novel_version_id is not None:
                stmt = stmt.where(StoryEntity.novel_version_id == novel_version_id)
            if entity_type:
                stmt = stmt.where(StoryEntity.entity_type == entity_type)
            stmt = stmt.order_by(StoryEntity.id.asc())
            return db.execute(stmt).scalars().all()
        finally:
            if should_close:
                db.close()

    def upsert_relation(
        self,
        novel_id: int,
        source: str,
        target: str,
        relation_type: str = "",
        description: str = "",
        sentiment: str = "neutral",
        chapter_num: int = 0,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> StoryRelation:
        """插入或更新关系。"""
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(StoryRelation).where(
                StoryRelation.novel_id == novel_id,
            )
            if novel_version_id is not None:
                stmt = stmt.where(StoryRelation.novel_version_id == novel_version_id)
            else:
                stmt = stmt.where(StoryRelation.novel_version_id.is_(None))
            stmt = stmt.where(
                StoryRelation.source == source,
                StoryRelation.target == target,
            )
            row = db.execute(stmt).scalar_one_or_none()
            if row:
                row.relation_type = relation_type
                row.description = description
                row.sentiment = sentiment
                row.chapter_num = chapter_num
            else:
                try:
                    with db.begin_nested():
                        row = StoryRelation(
                            novel_id=novel_id,
                            novel_version_id=novel_version_id,
                            source=source,
                            target=target,
                            relation_type=relation_type,
                            description=description,
                            sentiment=sentiment,
                            chapter_num=chapter_num,
                        )
                        db.add(row)
                        db.flush()
                except IntegrityError:
                    row = db.execute(stmt).scalar_one_or_none()
                    if row is None:
                        raise
                    row.relation_type = relation_type
                    row.description = description
                    row.sentiment = sentiment
                    row.chapter_num = chapter_num
            if should_close:
                db.commit()
            else:
                db.flush()
            db.refresh(row)
            return row
        except Exception:
            if should_close:
                db.rollback()
            raise
        finally:
            if should_close:
                db.close()

    def list_relations(
        self,
        novel_id: int,
        limit: int = 50,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> list[StoryRelation]:
        """列出关系。"""
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = (
                select(StoryRelation)
                .where(StoryRelation.novel_id == novel_id)
                .order_by(StoryRelation.chapter_num.desc(), StoryRelation.id.desc())
                .limit(max(1, limit))
            )
            if novel_version_id is not None:
                stmt = stmt.where(StoryRelation.novel_version_id == novel_version_id)
            else:
                stmt = stmt.where(StoryRelation.novel_version_id.is_(None))
            return db.execute(stmt).scalars().all()
        finally:
            if should_close:
                db.close()

    def suggest_foreshadows(
        self,
        chapter_text: str,
        novel_id: int,
        chapter_num: int,
        db: Optional[Session] = None,
    ) -> list[str]:
        """执行 suggest foreshadows 相关辅助逻辑。"""
        import re
        import logging as _logging
        _log = _logging.getLogger("app.services.memory")
        try:
            patterns = [
                r"(将要|誓要|决心|打算|一定会|终将|迟早)[^，。！？\n]{2,20}",
                r"(获得了|炼制了|取出了|祭出了|使出了)[^，。！？\n]{2,15}(法宝|秘宝|功法|神通|宝物|丹药)",
                r"(这[一件事个秘密段往事][^。！？\n]{0,30})",
            ]
            candidates: list[str] = []
            for pat in patterns:
                for m in re.finditer(pat, chapter_text):
                    candidates.append(m.group(0))
            if not candidates:
                return []
            should_close = db is None
            db = db or SessionLocal()
            try:
                existing_stmt = select(StoryForeshadow).where(
                    StoryForeshadow.novel_id == novel_id,
                    StoryForeshadow.planted_chapter == chapter_num,
                )
                existing_rows = db.execute(existing_stmt).scalars().all()
                existing_titles = {r.title or "" for r in existing_rows}
                inserted: list[str] = []
                for idx, candidate in enumerate(candidates):
                    if candidate in existing_titles:
                        continue
                    fid = f"H-{chapter_num}-{idx + 1}"
                    self.upsert_foreshadow(
                        novel_id=novel_id,
                        foreshadow_id=fid,
                        planted_chapter=chapter_num,
                        title=candidate,
                        state="planted",
                        db=db,
                    )
                    inserted.append(candidate)
                if should_close:
                    db.commit()
                else:
                    db.flush()
                return inserted
            except Exception as exc:
                if should_close:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                raise exc
            finally:
                if should_close:
                    db.close()
        except Exception as exc:
            _log.warning("suggest_foreshadows failed novel_id=%s chapter=%s error=%s", novel_id, chapter_num, exc)
            return []

    def list_recent_events(
        self,
        novel_id: int,
        chapter_num: int,
        limit: int = 30,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> list[StoryEvent]:
        """List recent events before (and including) a chapter."""
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(StoryEvent).where(StoryEvent.novel_id == novel_id)
            if novel_version_id is not None:
                stmt = stmt.where(StoryEvent.novel_version_id == novel_version_id)
            stmt = (
                stmt.where(StoryEvent.chapter_num <= chapter_num)
                .order_by(StoryEvent.chapter_num.desc(), StoryEvent.id.desc())
                .limit(max(1, limit))
            )
            return db.execute(stmt).scalars().all()
        finally:
            if should_close:
                db.close()


class CheckpointStore:
    """Durable checkpoint persistence for pipeline resume."""

    def save_checkpoint(
        self,
        task_id: str,
        novel_id: int,
        volume_no: int,
        chapter_num: int,
        node: str,
        state_json: dict,
        db: Optional[Session] = None,
    ) -> GenerationCheckpoint:
        """保存检查点。"""
        should_close = db is None
        db = db or SessionLocal()
        try:
            row = GenerationCheckpoint(
                task_id=task_id,
                novel_id=novel_id,
                volume_no=volume_no,
                chapter_num=chapter_num,
                node=node,
                state_json=state_json,
            )
            db.add(row)
            if should_close:
                db.commit()
            else:
                db.flush()
            db.refresh(row)
            return row
        except Exception:
            if should_close:
                db.rollback()
            raise
        finally:
            if should_close:
                db.close()


class QualityReportStore:
    """Quality report persistence helpers."""

    def add_report(
        self,
        novel_id: int,
        scope: str,
        scope_id: str,
        metrics_json: dict,
        verdict: str,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
    ) -> QualityReport:
        """执行 add report 相关辅助逻辑。"""
        should_close = db is None
        db = db or SessionLocal()
        try:
            row = QualityReport(
                novel_id=novel_id,
                novel_version_id=novel_version_id,
                scope=scope,
                scope_id=scope_id,
                metrics_json=metrics_json,
                verdict=verdict,
            )
            db.add(row)
            if should_close:
                db.commit()
            else:
                db.flush()
            db.refresh(row)
            return row
        except Exception:
            if should_close:
                db.rollback()
            raise
        finally:
            if should_close:
                db.close()

    def list_reports(
        self,
        novel_id: int,
        scope: str,
        scope_id: str | None = None,
        novel_version_id: int | None = None,
        db: Optional[Session] = None,
        limit: int | None = None,
    ) -> list[QualityReport]:
        """List reports by scope, optionally narrowed to scope_id.

        When *limit* is provided the query is capped at that many rows (most
        recent first by id).
        """
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(QualityReport).where(QualityReport.novel_id == novel_id, QualityReport.scope == scope)
            if novel_version_id is not None:
                stmt = stmt.where(QualityReport.novel_version_id == novel_version_id)
            if scope_id is not None:
                stmt = stmt.where(QualityReport.scope_id == scope_id)
            stmt = stmt.order_by(QualityReport.id.desc())
            if limit is not None:
                stmt = stmt.limit(limit)
            return db.execute(stmt).scalars().all()
        finally:
            if should_close:
                db.close()
