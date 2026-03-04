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
    novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True)
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
    novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True)
    chapter_num = Column(Integer, nullable=False)
    summary = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utc_now)


class NovelMemory(Base):
    """Novel-level memory (characters, world state, plot threads)."""

    __tablename__ = "novel_memory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True)
    memory_type = Column(String(50), nullable=False)  # character, world, plot
    key = Column(String(255), nullable=True)
    content = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class StoryCharacterProfile(Base):
    """Incremental hard-identity profile for a character during novel generation."""

    __tablename__ = "story_character_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True)
    novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True)
    character_key = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=False)
    gender_presentation = Column(String(64), nullable=True)
    age_band = Column(String(64), nullable=True)
    skin_tone = Column(String(64), nullable=True)
    ethnicity = Column(String(64), nullable=True)
    body_type = Column(String(128), nullable=True)
    face_features = Column(String(255), nullable=True)
    hair_style = Column(String(128), nullable=True)
    hair_color = Column(String(64), nullable=True)
    eye_color = Column(String(64), nullable=True)
    wardrobe_base_style = Column(String(255), nullable=True)
    signature_items_json = Column(JSON, default=list)
    visual_do_not_change_json = Column(JSON, default=list)
    evidence_json = Column(JSON, default=list)
    confidence = Column(Float, default=0.0)
    updated_chapter_num = Column(Integer, nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class ChapterEmbedding(Base):
    """Chapter content embeddings for vector search."""

    __tablename__ = "chapter_embeddings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True)
    chapter_num = Column(Integer, nullable=False)
    content_hash = Column(String(64), nullable=True)
    embedding = Column(EMBEDDING_COLUMN_TYPE, nullable=True)
    created_at = Column(DateTime, default=_utc_now)


class KnowledgeChunk(Base):
    """Vector store chunk for RAG."""

    __tablename__ = "knowledge_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True)
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
    status = Column(String(50), default="pending")  # submitted,running,retrying,paused,cancelled,completed,failed
    run_state = Column(String(32), default="submitted")
    step = Column(String(50), nullable=True)  # legacy
    current_phase = Column(String(50), nullable=True)
    current_chapter = Column(Integer, default=0)
    total_chapters = Column(Integer, default=0)
    progress = Column(Float, default=0.0)
    message = Column(String(500), nullable=True)
    error = Column(Text, nullable=True)
    error_code = Column(String(100), nullable=True)
    error_category = Column(String(32), nullable=True)  # transient, permanent, policy
    retryable = Column(Integer, default=0)  # sqlite-friendly bool
    idempotency_key = Column(String(128), nullable=True, index=True)
    trace_id = Column(String(64), nullable=True, index=True)
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
    novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True)
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
    novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True)
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
    novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True)
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
    novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True)
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
    novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True)
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
    novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True)
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


class User(Base):
    """Application user."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    uuid = Column(String(36), unique=True, default=_uuid_default, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(32), nullable=False, default="user")  # admin, user
    status = Column(String(32), nullable=False, default="active")  # active, disabled, pending_activation
    email_verified_at = Column(DateTime, nullable=True)
    last_login_at = Column(DateTime, nullable=True)
    password_updated_at = Column(DateTime, nullable=True)
    failed_login_count = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class UserQuota(Base):
    """User quota and plan limits."""

    __tablename__ = "user_quotas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    plan_key = Column(String(32), nullable=False, default="free")  # free, pro, team
    max_concurrent_tasks = Column(Integer, nullable=False, default=1)
    monthly_chapter_limit = Column(Integer, nullable=False, default=1_000_000)
    monthly_token_limit = Column(Integer, nullable=False, default=10_000_000_000)
    status = Column(String(32), nullable=False, default="active")  # active, suspended
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class UsageLedger(Base):
    """Per-task usage and billing ledger."""

    __tablename__ = "usage_ledger"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=True, index=True)
    task_id = Column(String(255), nullable=False, index=True)
    source = Column(String(32), nullable=False, default="generation")  # generation, rewrite
    input_tokens = Column(Integer, nullable=False, default=0)
    output_tokens = Column(Integer, nullable=False, default=0)
    chapters_generated = Column(Integer, nullable=False, default=0)
    estimated_cost = Column(Float, nullable=False, default=0.0)
    created_at = Column(DateTime, default=_utc_now)


class EmailVerificationToken(Base):
    """Email verification token."""

    __tablename__ = "email_verification_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(128), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utc_now)


class PasswordResetToken(Base):
    """Password reset token."""

    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(128), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utc_now)


class AdminAuditLog(Base):
    """Admin action audit trail."""

    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    target_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(64), nullable=False)
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)


class SystemModelProvider(Base):
    """Admin-managed model provider configuration."""

    __tablename__ = "system_model_providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_key = Column(String(64), nullable=False, unique=True, index=True)
    display_name = Column(String(128), nullable=False)
    adapter_type = Column(String(64), nullable=False, default="openai_compatible")
    base_url = Column(String(512), nullable=True)
    api_key_ciphertext = Column(Text, nullable=True)
    api_key_is_encrypted = Column(Integer, nullable=False, default=0)  # sqlite-friendly bool
    is_enabled = Column(Integer, nullable=False, default=1)  # sqlite-friendly bool
    priority = Column(Integer, nullable=False, default=100)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class SystemModelDefinition(Base):
    """Admin-managed model definition under a provider."""

    __tablename__ = "system_model_definitions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    provider_id = Column(Integer, ForeignKey("system_model_providers.id", ondelete="CASCADE"), nullable=False, index=True)
    model_name = Column(String(255), nullable=False)
    model_type = Column(String(32), nullable=False, default="chat")  # chat, embedding, image, video
    is_default = Column(Integer, nullable=False, default=0)  # sqlite-friendly bool
    is_enabled = Column(Integer, nullable=False, default=1)  # sqlite-friendly bool
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class SystemRuntimeSetting(Base):
    """Admin-managed runtime setting override with env fallback."""

    __tablename__ = "system_runtime_settings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    setting_key = Column(String(128), nullable=False, unique=True, index=True)
    setting_value_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class NovelVersion(Base):
    """Versioned snapshot of a novel."""

    __tablename__ = "novel_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    version_no = Column(Integer, nullable=False)
    parent_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="SET NULL"), nullable=True)
    source_task_id = Column(String(255), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="draft")  # draft, generating, completed, failed
    is_default = Column(Integer, nullable=False, default=0)  # sqlite-friendly bool
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class ChapterVersion(Base):
    """Versioned chapter content."""

    __tablename__ = "chapter_versions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=False)
    chapter_num = Column(Integer, nullable=False)
    title = Column(String(255), nullable=True)
    content = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="pending")  # pending, generating, completed, failed
    review_score = Column(Float, nullable=True)
    language_quality_score = Column(Float, nullable=True)
    language_quality_report = Column(Text, nullable=True)
    metadata_ = Column("metadata", JSON, default=dict)
    source_chapter_version_id = Column(Integer, ForeignKey("chapter_versions.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class RewriteRequest(Base):
    """Human-in-the-loop chapter rewrite request."""

    __tablename__ = "rewrite_requests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    base_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=False)
    target_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=False)
    task_id = Column(String(255), nullable=True, index=True)
    status = Column(String(32), nullable=False, default="submitted")  # submitted, running, completed, failed, cancelled
    rewrite_from_chapter = Column(Integer, nullable=False)
    rewrite_to_chapter = Column(Integer, nullable=False)
    current_chapter = Column(Integer, nullable=True)
    progress = Column(Float, default=0.0)
    message = Column(String(500), nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utc_now)
    updated_at = Column(DateTime, default=_utc_now, onupdate=_utc_now)


class RewriteAnnotation(Base):
    """Fine-grained rewrite annotations attached to a rewrite request."""

    __tablename__ = "rewrite_annotations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    rewrite_request_id = Column(Integer, ForeignKey("rewrite_requests.id", ondelete="CASCADE"), nullable=False)
    novel_id = Column(Integer, ForeignKey("novels.id", ondelete="CASCADE"), nullable=False)
    base_version_id = Column(Integer, ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=False)
    chapter_num = Column(Integer, nullable=False)
    start_offset = Column(Integer, nullable=True)
    end_offset = Column(Integer, nullable=True)
    selected_text = Column(Text, nullable=True)
    issue_type = Column(String(32), nullable=False, default="other")  # bug, continuity, style, pace, other
    instruction = Column(Text, nullable=False)
    priority = Column(String(16), nullable=False, default="should")  # must, should, nice
    metadata_ = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=_utc_now)


Index("idx_chapter_summaries_novel_chapter", ChapterSummary.novel_version_id, ChapterSummary.chapter_num, unique=True)
Index("idx_novel_memory_novel_type_key", NovelMemory.novel_version_id, NovelMemory.memory_type, NovelMemory.key, unique=True)
Index("idx_novel_specifications_novel_type", NovelSpecification.novel_id, NovelSpecification.spec_type, unique=True)
Index("idx_story_entities_novel_type_name", StoryEntity.novel_version_id, StoryEntity.entity_type, StoryEntity.name, unique=True)
Index("idx_story_facts_novel_entity_type", StoryFact.novel_version_id, StoryFact.entity_id, StoryFact.fact_type)
Index("idx_story_events_novel_chapter", StoryEvent.novel_version_id, StoryEvent.chapter_num)
Index("idx_story_foreshadows_novel_state", StoryForeshadow.novel_version_id, StoryForeshadow.state)
Index("idx_story_foreshadows_novel_foreshadow", StoryForeshadow.novel_version_id, StoryForeshadow.foreshadow_id, unique=True)
Index("idx_story_snapshots_novel_volume", StorySnapshot.novel_version_id, StorySnapshot.volume_no)
Index("idx_generation_checkpoints_task_node", GenerationCheckpoint.task_id, GenerationCheckpoint.node)
Index("idx_quality_reports_novel_scope_scopeid", QualityReport.novel_version_id, QualityReport.scope, QualityReport.scope_id)
Index("idx_story_character_profiles_novel_character", StoryCharacterProfile.novel_version_id, StoryCharacterProfile.character_key, unique=True)
Index("idx_novel_feedback_novel_chapter_volume", NovelFeedback.novel_id, NovelFeedback.chapter_num, NovelFeedback.volume_no)
Index("idx_novel_versions_novel_version", NovelVersion.novel_id, NovelVersion.version_no, unique=True)
Index("idx_novel_versions_novel_default", NovelVersion.novel_id, NovelVersion.is_default)
Index("idx_chapter_versions_version_chapter", ChapterVersion.novel_version_id, ChapterVersion.chapter_num, unique=True)
Index("idx_chapter_outlines_version_chapter", ChapterOutline.novel_version_id, ChapterOutline.chapter_num, unique=True)
Index("idx_chapter_embeddings_version_chapter", ChapterEmbedding.novel_version_id, ChapterEmbedding.chapter_num, unique=True)
Index("idx_knowledge_chunks_version_type", KnowledgeChunk.novel_version_id, KnowledgeChunk.chunk_type)
Index("idx_rewrite_requests_novel_status", RewriteRequest.novel_id, RewriteRequest.status)
Index("idx_rewrite_annotations_request_chapter", RewriteAnnotation.rewrite_request_id, RewriteAnnotation.chapter_num)
Index("idx_users_email", User.email, unique=True)
Index("idx_users_role_status", User.role, User.status)
Index("idx_users_status_locked", User.status, User.locked_until)
Index("idx_system_model_providers_priority", SystemModelProvider.priority)
Index("idx_system_model_definitions_provider_type", SystemModelDefinition.provider_id, SystemModelDefinition.model_type)
Index(
    "idx_system_model_definitions_provider_name_type",
    SystemModelDefinition.provider_id,
    SystemModelDefinition.model_name,
    SystemModelDefinition.model_type,
    unique=True,
)
Index("idx_system_runtime_settings_key", SystemRuntimeSetting.setting_key, unique=True)
Index("idx_user_quotas_user_plan", UserQuota.user_id, UserQuota.plan_key, unique=True)
Index("idx_usage_ledger_user_created", UsageLedger.user_id, UsageLedger.created_at)
Index("idx_admin_audit_logs_action", AdminAuditLog.action)
