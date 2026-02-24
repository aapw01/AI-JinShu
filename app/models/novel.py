"""Novel and Chapter models."""
import uuid
import os
from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, JSON, Float, Index
from pgvector.sqlalchemy import Vector

from app.core.database import Base

# Support both postgresql:// and postgres:// URL schemes
_db_url = os.getenv("DATABASE_URL", "")
EMBEDDING_COLUMN_TYPE = Vector(1536) if "postgres" in _db_url.lower() else Text


def _uuid_default():
    return str(uuid.uuid4())


def _utc_now():
    return datetime.now(timezone.utc)


class Novel(Base):
    """Novel metadata and config."""

    __tablename__ = "novels"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uuid = Column(String(36), unique=True, default=_uuid_default, index=True)
    user_id = Column(String(255), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    target_language = Column(String(20), default="zh")
    native_style_profile = Column(Text, nullable=True)
    genre = Column(String(100), nullable=True)
    style = Column(String(100), nullable=True)
    pace = Column(String(50), nullable=True)
    audience = Column(String(100), nullable=True)
    target_length = Column(String(50), nullable=True)
    writing_method = Column(String(100), nullable=True)
    strategy = Column(String(100), nullable=True)
    user_idea = Column(Text, nullable=True)
    inspiration_tags = Column(JSON, nullable=True)
    config = Column(JSON, default=dict)
    status = Column(String(50), default="draft")
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class NovelSpecification(Base):
    """Novel specification (architecture, world, characters)."""

    __tablename__ = "novel_specifications"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    spec_type = Column(String(50), nullable=False)  # architecture, world, characters
    content = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class ChapterOutline(Base):
    """Chapter outline before writing."""

    __tablename__ = "chapter_outlines"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    chapter_num = Column(Integer, nullable=False)
    title = Column(String(255), nullable=True)
    outline = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)


class Chapter(Base):
    """Chapter content and metadata."""

    __tablename__ = "chapters"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    chapter_num = Column(Integer, nullable=False)
    title = Column(String(255), nullable=True)
    content = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    status = Column(String(50), default="pending")  # pending/generating/reviewing/finalizing/completed/failed
    review_score = Column(Float, nullable=True)
    language_quality_score = Column(Float, nullable=True)
    language_quality_report = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class NovelPreset(Base):
    """User-selected preset for a novel."""

    __tablename__ = "novel_presets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    preset_key = Column(String(100), nullable=False)
    preset_data = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)


class ChapterSummary(Base):
    """Chapter summary for memory/context."""

    __tablename__ = "chapter_summaries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    chapter_num = Column(Integer, nullable=False)
    summary = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utc_now)


class NovelMemory(Base):
    """Novel-level memory (characters, world state, plot threads)."""

    __tablename__ = "novel_memory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    memory_type = Column(String(50), nullable=False)  # character, world, plot
    key = Column(String(255), nullable=True)
    content = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class ChapterEmbedding(Base):
    """Chapter content embeddings for vector search."""

    __tablename__ = "chapter_embeddings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    chapter_num = Column(Integer, nullable=False)
    content_hash = Column(String(64), nullable=True)
    embedding = Column(EMBEDDING_COLUMN_TYPE, nullable=True)
    created_at = Column(DateTime, default=_utc_now)


class KnowledgeChunk(Base):
    """Vector store chunk for RAG."""

    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)
    chunk_type = Column(String(50), nullable=True)
    embedding = Column(EMBEDDING_COLUMN_TYPE, nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)


class GenerationTask(Base):
    """Generation task persistence."""

    __tablename__ = "generation_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(255), unique=True, nullable=False, index=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    status = Column(String(50), default="pending")  # pending, running, completed, failed
    step = Column(String(50), nullable=True)  # legacy
    current_phase = Column(String(50), nullable=True)
    current_chapter = Column(Integer, default=0)
    total_chapters = Column(Integer, default=0)
    progress = Column(Float, default=0.0)
    message = Column(String(500), nullable=True)
    error = Column(Text, nullable=True)
    token_usage_input = Column(Integer, default=0)
    token_usage_output = Column(Integer, default=0)
    estimated_cost = Column(Float, default=0.0)
    outline_confirmed = Column(Integer, default=1)  # 0 false / 1 true, sqlite-friendly
    final_report = Column(JSON, default=dict)
    num_chapters = Column(Integer, default=1)
    start_chapter = Column(Integer, default=1)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class ChapterAnnotation(Base):
    """Chapter annotations (highlights, notes)."""

    __tablename__ = "chapter_annotations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    chapter_num = Column(Integer, nullable=False)
    annotation_type = Column(String(50), nullable=False)  # highlight, note
    start_offset = Column(Integer, nullable=True)
    end_offset = Column(Integer, nullable=True)
    content = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)


class StoryEntity(Base):
    """Structured entity in story bible (character/location/org/item/rule)."""

    __tablename__ = "story_entities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    entity_type = Column(String(50), nullable=False)  # character, location, organization, item, rule
    name = Column(String(255), nullable=False)
    status = Column(String(50), nullable=True)
    summary = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)
    revision = Column(Integer, default=1)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StoryFact(Base):
    """Versioned fact records attached to an entity."""

    __tablename__ = "story_facts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    entity_id = Column(Integer, ForeignKey("story_entities.id", ondelete="CASCADE"), nullable=False)
    fact_type = Column(String(100), nullable=False)
    value_json = Column(JSON, default=dict)
    chapter_from = Column(Integer, nullable=False)
    chapter_to = Column(Integer, nullable=True)
    revision = Column(Integer, default=1)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StoryEvent(Base):
    """Structured story event with causal links."""

    __tablename__ = "story_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String(64), nullable=False, index=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    chapter_num = Column(Integer, nullable=False)
    title = Column(String(255), nullable=True)
    event_type = Column(String(100), nullable=True)
    actors = Column(JSON, default=list)
    causes = Column(JSON, default=list)
    effects = Column(JSON, default=list)
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StoryForeshadow(Base):
    """Foreshadowing lifecycle tracking."""

    __tablename__ = "story_foreshadows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    foreshadow_id = Column(String(64), nullable=False, index=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=True)
    planted_chapter = Column(Integer, nullable=False)
    resolved_chapter = Column(Integer, nullable=True)
    state = Column(String(32), default="planted")  # planted, resolved, expired
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StorySnapshot(Base):
    """Volume-level canonical snapshot for long-form generation."""

    __tablename__ = "story_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    volume_no = Column(Integer, nullable=False)
    chapter_end = Column(Integer, nullable=False)
    snapshot_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)


class GenerationCheckpoint(Base):
    """Durable checkpoint for resuming generation graph."""

    __tablename__ = "generation_checkpoints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(255), nullable=False, index=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    volume_no = Column(Integer, nullable=False)
    chapter_num = Column(Integer, nullable=False)
    node = Column(String(100), nullable=False)
    state_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class QualityReport(Base):
    """Quality reports for chapter/volume/book scopes."""

    __tablename__ = "quality_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    scope = Column(String(20), nullable=False)  # chapter, volume, book
    scope_id = Column(String(64), nullable=False)
    metrics_json = Column(JSON, default=dict)
    verdict = Column(String(32), nullable=False, default="unknown")  # pass, fail, warning
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class NovelFeedback(Base):
    """Human feedback loop records for generated chapters/volumes."""

    __tablename__ = "novel_feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    chapter_num = Column(Integer, nullable=True)
    volume_no = Column(Integer, nullable=True)
    feedback_type = Column(String(32), nullable=False, default="editor")  # editor, reader
    rating = Column(Float, nullable=True)  # 0-1 normalized
    tags = Column(JSON, default=list)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


Index("idx_story_entities_novel_type_name", StoryEntity.novel_id, StoryEntity.entity_type, StoryEntity.name)
Index("idx_story_facts_novel_entity_type", StoryFact.novel_id, StoryFact.entity_id, StoryFact.fact_type)
Index("idx_story_events_novel_chapter", StoryEvent.novel_id, StoryEvent.chapter_num)
Index("idx_story_foreshadows_novel_state", StoryForeshadow.novel_id, StoryForeshadow.state)
Index("idx_story_snapshots_novel_volume", StorySnapshot.novel_id, StorySnapshot.volume_no)
Index("idx_generation_checkpoints_task_node", GenerationCheckpoint.task_id, GenerationCheckpoint.node)
Index("idx_quality_reports_novel_scope_scopeid", QualityReport.novel_id, QualityReport.scope, QualityReport.scope_id)
Index("idx_novel_feedback_novel_chapter_volume", NovelFeedback.novel_id, NovelFeedback.chapter_num, NovelFeedback.volume_no)
