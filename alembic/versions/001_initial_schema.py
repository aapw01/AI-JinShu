"""initial schema – consolidated from migrations 001-021

Revision ID: 001
Revises: (none)
Create Date: 2026-03-14 21:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # pgvector extension
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── novels ──────────────────────────────────────────────────────────
    op.create_table(
        "novels",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("uuid", sa.String(36), unique=True, index=True),
        sa.Column("user_id", sa.String(255), nullable=True, index=True),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("target_language", sa.String(20), server_default="zh"),
        sa.Column("native_style_profile", sa.Text, nullable=True),
        sa.Column("genre", sa.String(100), nullable=True),
        sa.Column("style", sa.String(100), nullable=True),
        sa.Column("pace", sa.String(50), nullable=True),
        sa.Column("audience", sa.String(100), nullable=True),
        sa.Column("target_length", sa.String(50), nullable=True),
        sa.Column("writing_method", sa.String(100), nullable=True),
        sa.Column("strategy", sa.String(100), nullable=True),
        sa.Column("user_idea", sa.Text, nullable=True),
        sa.Column("inspiration_tags", sa.JSON, nullable=True),
        sa.Column("config", sa.JSON, server_default="{}"),
        sa.Column("status", sa.String(50), server_default="draft"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )

    # ── novel_versions ──────────────────────────────────────────────────
    op.create_table(
        "novel_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("parent_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_task_id", sa.String(255), nullable=True, index=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("is_default", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_novel_versions_novel_version", "novel_versions", ["novel_id", "version_no"], unique=True)
    op.create_index("idx_novel_versions_novel_default", "novel_versions", ["novel_id", "is_default"])

    # ── chapter_versions ────────────────────────────────────────────────
    op.create_table(
        "chapter_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_num", sa.Integer, nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("content", sa.Text, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("review_score", sa.Float, nullable=True),
        sa.Column("language_quality_score", sa.Float, nullable=True),
        sa.Column("language_quality_report", sa.Text, nullable=True),
        sa.Column("metadata", sa.JSON, server_default="{}"),
        sa.Column("source_chapter_version_id", sa.Integer, sa.ForeignKey("chapter_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_chapter_versions_version_chapter", "chapter_versions", ["novel_version_id", "chapter_num"], unique=True)

    # ── novel_specifications ────────────────────────────────────────────
    op.create_table(
        "novel_specifications",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("spec_type", sa.String(50), nullable=False),
        sa.Column("content", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_novel_specifications_novel_type", "novel_specifications", ["novel_id", "spec_type"], unique=True)

    # ── chapter_outlines ────────────────────────────────────────────────
    op.create_table(
        "chapter_outlines",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("chapter_num", sa.Integer, nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("outline", sa.Text, nullable=True),
        sa.Column("metadata", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_chapter_outlines_version_chapter", "chapter_outlines", ["novel_version_id", "chapter_num"], unique=True)

    # ── novel_presets ───────────────────────────────────────────────────
    op.create_table(
        "novel_presets",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("preset_key", sa.String(100), nullable=False),
        sa.Column("preset_data", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )

    # ── chapter_summaries ───────────────────────────────────────────────
    op.create_table(
        "chapter_summaries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("chapter_num", sa.Integer, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_chapter_summaries_novel_chapter", "chapter_summaries", ["novel_version_id", "chapter_num"], unique=True)

    # ── novel_memory ────────────────────────────────────────────────────
    op.create_table(
        "novel_memory",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("memory_type", sa.String(50), nullable=False),
        sa.Column("key", sa.String(255), nullable=True),
        sa.Column("content", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_novel_memory_novel_type_key", "novel_memory", ["novel_version_id", "memory_type", "key"], unique=True)

    # ── story_character_profiles ────────────────────────────────────────
    op.create_table(
        "story_character_profiles",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("character_key", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("gender_presentation", sa.String(64), nullable=True),
        sa.Column("age_band", sa.String(64), nullable=True),
        sa.Column("skin_tone", sa.String(64), nullable=True),
        sa.Column("ethnicity", sa.String(64), nullable=True),
        sa.Column("body_type", sa.String(128), nullable=True),
        sa.Column("face_features", sa.String(255), nullable=True),
        sa.Column("hair_style", sa.String(128), nullable=True),
        sa.Column("hair_color", sa.String(64), nullable=True),
        sa.Column("eye_color", sa.String(64), nullable=True),
        sa.Column("wardrobe_base_style", sa.String(255), nullable=True),
        sa.Column("signature_items_json", sa.JSON, server_default="[]"),
        sa.Column("visual_do_not_change_json", sa.JSON, server_default="[]"),
        sa.Column("evidence_json", sa.JSON, server_default="[]"),
        sa.Column("confidence", sa.Float, server_default="0.0"),
        sa.Column("updated_chapter_num", sa.Integer, nullable=True),
        sa.Column("metadata", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_story_character_profiles_novel_character", "story_character_profiles", ["novel_version_id", "character_key"], unique=True)

    # ── chapter_embeddings ──────────────────────────────────────────────
    op.create_table(
        "chapter_embeddings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("chapter_num", sa.Integer, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("embedding", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_chapter_embeddings_version_chapter", "chapter_embeddings", ["novel_version_id", "chapter_num"], unique=True)
    op.execute("ALTER TABLE chapter_embeddings ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector(1536)")

    # ── knowledge_chunks ────────────────────────────────────────────────
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("chunk_type", sa.String(50), nullable=True),
        sa.Column("embedding", sa.Text, nullable=True),
        sa.Column("metadata", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_knowledge_chunks_version_type", "knowledge_chunks", ["novel_version_id", "chunk_type"])
    op.execute("ALTER TABLE knowledge_chunks ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector(1536)")

    # ── generation_tasks ────────────────────────────────────────────────
    op.create_table(
        "generation_tasks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column("run_state", sa.String(32), server_default="submitted"),
        sa.Column("step", sa.String(50), nullable=True),
        sa.Column("current_phase", sa.String(50), nullable=True),
        sa.Column("current_chapter", sa.Integer, server_default="0"),
        sa.Column("total_chapters", sa.Integer, server_default="0"),
        sa.Column("progress", sa.Float, server_default="0.0"),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_category", sa.String(32), nullable=True),
        sa.Column("retryable", sa.Integer, server_default="0"),
        sa.Column("idempotency_key", sa.String(128), nullable=True, index=True),
        sa.Column("trace_id", sa.String(64), nullable=True, index=True),
        sa.Column("token_usage_input", sa.Integer, server_default="0"),
        sa.Column("token_usage_output", sa.Integer, server_default="0"),
        sa.Column("estimated_cost", sa.Float, server_default="0.0"),
        sa.Column("outline_confirmed", sa.Integer, server_default="1"),
        sa.Column("final_report", sa.JSON, server_default="{}"),
        sa.Column("num_chapters", sa.Integer, server_default="1"),
        sa.Column("start_chapter", sa.Integer, server_default="1"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )

    # ── chapter_annotations ─────────────────────────────────────────────
    op.create_table(
        "chapter_annotations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_num", sa.Integer, nullable=False),
        sa.Column("annotation_type", sa.String(50), nullable=False),
        sa.Column("start_offset", sa.Integer, nullable=True),
        sa.Column("end_offset", sa.Integer, nullable=True),
        sa.Column("content", sa.Text, nullable=True),
        sa.Column("metadata", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )

    # ── story bible tables ──────────────────────────────────────────────
    op.create_table(
        "story_entities",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("entity_type", sa.String(50), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("metadata", sa.JSON, server_default="{}"),
        sa.Column("revision", sa.Integer, server_default="1"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_story_entities_novel_type_name", "story_entities", ["novel_version_id", "entity_type", "name"], unique=True)

    op.create_table(
        "story_facts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("entity_id", sa.Integer, sa.ForeignKey("story_entities.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fact_type", sa.String(100), nullable=False),
        sa.Column("value_json", sa.JSON, server_default="{}"),
        sa.Column("chapter_from", sa.Integer, nullable=False),
        sa.Column("chapter_to", sa.Integer, nullable=True),
        sa.Column("revision", sa.Integer, server_default="1"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_story_facts_novel_entity_type", "story_facts", ["novel_version_id", "entity_id", "fact_type"])

    op.create_table(
        "story_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(64), nullable=False, index=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("chapter_num", sa.Integer, nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("event_type", sa.String(100), nullable=True),
        sa.Column("actors", sa.JSON, server_default="[]"),
        sa.Column("causes", sa.JSON, server_default="[]"),
        sa.Column("effects", sa.JSON, server_default="[]"),
        sa.Column("payload", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_story_events_novel_chapter", "story_events", ["novel_version_id", "chapter_num"])

    op.create_table(
        "story_foreshadows",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("foreshadow_id", sa.String(64), nullable=False, index=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("planted_chapter", sa.Integer, nullable=False),
        sa.Column("resolved_chapter", sa.Integer, nullable=True),
        sa.Column("state", sa.String(32), server_default="planted"),
        sa.Column("payload", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_story_foreshadows_novel_state", "story_foreshadows", ["novel_version_id", "state"])
    op.create_index("idx_story_foreshadows_novel_foreshadow", "story_foreshadows", ["novel_version_id", "foreshadow_id"], unique=True)

    op.create_table(
        "story_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("volume_no", sa.Integer, nullable=False),
        sa.Column("chapter_end", sa.Integer, nullable=False),
        sa.Column("snapshot_json", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_story_snapshots_novel_volume", "story_snapshots", ["novel_version_id", "volume_no"])

    # ── generation_checkpoints ──────────────────────────────────────────
    op.create_table(
        "generation_checkpoints",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("task_id", sa.String(255), nullable=False, index=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("volume_no", sa.Integer, nullable=False),
        sa.Column("chapter_num", sa.Integer, nullable=False),
        sa.Column("node", sa.String(100), nullable=False),
        sa.Column("state_json", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_generation_checkpoints_task_node", "generation_checkpoints", ["task_id", "node"])

    # ── quality_reports ─────────────────────────────────────────────────
    op.create_table(
        "quality_reports",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("scope_id", sa.String(64), nullable=False),
        sa.Column("metrics_json", sa.JSON, server_default="{}"),
        sa.Column("verdict", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_quality_reports_novel_scope_scopeid", "quality_reports", ["novel_version_id", "scope", "scope_id"])

    # ── novel_feedback ──────────────────────────────────────────────────
    op.create_table(
        "novel_feedback",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_num", sa.Integer, nullable=True),
        sa.Column("volume_no", sa.Integer, nullable=True),
        sa.Column("feedback_type", sa.String(32), nullable=False, server_default="editor"),
        sa.Column("rating", sa.Float, nullable=True),
        sa.Column("tags", sa.JSON, server_default="[]"),
        sa.Column("comment", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_novel_feedback_novel_chapter_volume", "novel_feedback", ["novel_id", "chapter_num", "volume_no"])

    # ── rewrite tables ──────────────────────────────────────────────────
    op.create_table(
        "rewrite_requests",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("base_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("target_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", sa.String(255), nullable=True, index=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="submitted"),
        sa.Column("rewrite_from_chapter", sa.Integer, nullable=False),
        sa.Column("rewrite_to_chapter", sa.Integer, nullable=False),
        sa.Column("current_chapter", sa.Integer, nullable=True),
        sa.Column("progress", sa.Float, server_default="0.0"),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_rewrite_requests_novel_status", "rewrite_requests", ["novel_id", "status"])

    op.create_table(
        "rewrite_annotations",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("rewrite_request_id", sa.Integer, sa.ForeignKey("rewrite_requests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("base_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_num", sa.Integer, nullable=False),
        sa.Column("start_offset", sa.Integer, nullable=True),
        sa.Column("end_offset", sa.Integer, nullable=True),
        sa.Column("selected_text", sa.Text, nullable=True),
        sa.Column("issue_type", sa.String(32), nullable=False, server_default="other"),
        sa.Column("instruction", sa.Text, nullable=False),
        sa.Column("priority", sa.String(16), nullable=False, server_default="should"),
        sa.Column("metadata", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_rewrite_annotations_request_chapter", "rewrite_annotations", ["rewrite_request_id", "chapter_num"])

    # ── auth / users ────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("uuid", sa.String(36), unique=True, index=True),
        sa.Column("email", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="user"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("email_verified_at", sa.DateTime, nullable=True),
        sa.Column("last_login_at", sa.DateTime, nullable=True),
        sa.Column("password_updated_at", sa.DateTime, nullable=True),
        sa.Column("failed_login_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_users_email", "users", ["email"], unique=True)
    op.create_index("idx_users_role_status", "users", ["role", "status"])
    op.create_index("idx_users_status_locked", "users", ["status", "locked_until"])

    op.create_table(
        "user_quotas",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("plan_key", sa.String(32), nullable=False, server_default="free"),
        sa.Column("max_concurrent_tasks", sa.Integer, nullable=False, server_default="1"),
        sa.Column("monthly_chapter_limit", sa.BigInteger, nullable=False, server_default="1000000"),
        sa.Column("monthly_token_limit", sa.BigInteger, nullable=False, server_default="10000000000"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_user_quotas_user_plan", "user_quotas", ["user_id", "plan_key"], unique=True)

    op.create_table(
        "usage_ledger",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("task_id", sa.String(255), nullable=False, index=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="generation"),
        sa.Column("input_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer, nullable=False, server_default="0"),
        sa.Column("chapters_generated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("estimated_cost", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_usage_ledger_user_created", "usage_ledger", ["user_id", "created_at"])

    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("used_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("expires_at", sa.DateTime, nullable=False),
        sa.Column("used_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )

    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("actor_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("target_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("metadata", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_admin_audit_logs_action", "admin_audit_logs", ["action"])

    # ── system settings ─────────────────────────────────────────────────
    op.create_table(
        "system_model_providers",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("provider_key", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("adapter_type", sa.String(64), nullable=False, server_default="openai_compatible"),
        sa.Column("base_url", sa.String(512), nullable=True),
        sa.Column("api_key_ciphertext", sa.Text, nullable=True),
        sa.Column("api_key_is_encrypted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_enabled", sa.Integer, nullable=False, server_default="1"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_system_model_providers_priority", "system_model_providers", ["priority"])

    op.create_table(
        "system_model_definitions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("provider_id", sa.Integer, sa.ForeignKey("system_model_providers.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("model_name", sa.String(255), nullable=False),
        sa.Column("model_type", sa.String(32), nullable=False, server_default="chat"),
        sa.Column("is_default", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_enabled", sa.Integer, nullable=False, server_default="1"),
        sa.Column("metadata", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_system_model_definitions_provider_type", "system_model_definitions", ["provider_id", "model_type"])
    op.create_index("idx_system_model_definitions_provider_name_type", "system_model_definitions", ["provider_id", "model_name", "model_type"], unique=True)

    op.create_table(
        "system_runtime_settings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("setting_key", sa.String(128), nullable=False, unique=True, index=True),
        sa.Column("setting_value_json", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_system_runtime_settings_key", "system_runtime_settings", ["setting_key"], unique=True)

    # ── creation_tasks (unified scheduler) ──────────────────────────────
    op.create_table(
        "creation_tasks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("public_id", sa.String(36), unique=True, nullable=False, index=True),
        sa.Column("user_uuid", sa.String(36), nullable=False, index=True),
        sa.Column("task_type", sa.String(32), nullable=False),
        sa.Column("resource_type", sa.String(32), nullable=False),
        sa.Column("resource_id", sa.Integer, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
        sa.Column("queue_seq", sa.BigInteger, nullable=True, index=True),
        sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer, nullable=False, server_default="3"),
        sa.Column("worker_task_id", sa.String(255), nullable=True, index=True),
        sa.Column("phase", sa.String(64), nullable=True),
        sa.Column("progress", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_category", sa.String(32), nullable=True),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("payload_json", sa.JSON, server_default="{}"),
        sa.Column("result_json", sa.JSON, server_default="{}"),
        sa.Column("resume_cursor_json", sa.JSON, server_default="{}"),
        sa.Column("last_heartbeat_at", sa.DateTime, nullable=True),
        sa.Column("worker_lease_expires_at", sa.DateTime, nullable=True),
        sa.Column("recovery_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("finished_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_creation_tasks_user_status_queue", "creation_tasks", ["user_uuid", "status", "queue_seq"])
    op.create_index("idx_creation_tasks_type_resource", "creation_tasks", ["task_type", "resource_type", "resource_id"])

    op.create_table(
        "creation_task_checkpoints",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("creation_task_id", sa.Integer, sa.ForeignKey("creation_tasks.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("unit_type", sa.String(32), nullable=False, server_default="chapter"),
        sa.Column("unit_no", sa.Integer, nullable=False),
        sa.Column("partition", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="completed"),
        sa.Column("payload_json", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_creation_task_checkpoints_task_unit_partition", "creation_task_checkpoints", ["creation_task_id", "unit_type", "partition", "unit_no"])
    op.create_index("uq_creation_task_checkpoints_task_unit_partition", "creation_task_checkpoints", ["creation_task_id", "unit_type", "unit_no", "partition"], unique=True)

    # ── storyboard tables ───────────────────────────────────────────────
    op.create_table(
        "storyboard_projects",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("uuid", sa.String(36), unique=True, index=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("source_novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("owner_user_uuid", sa.String(36), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("target_episodes", sa.Integer, nullable=False, server_default="40"),
        sa.Column("target_episode_seconds", sa.Integer, nullable=False, server_default="90"),
        sa.Column("style_profile", sa.String(100), nullable=True),
        sa.Column("professional_mode", sa.Integer, nullable=False, server_default="1"),
        sa.Column("audience_goal", sa.String(100), nullable=True),
        sa.Column("output_lanes", sa.JSON, nullable=True),
        sa.Column("active_lane", sa.String(32), nullable=False, server_default="vertical_feed"),
        sa.Column("config_json", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )

    op.create_table(
        "storyboard_source_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("storyboard_project_id", sa.Integer, sa.ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("snapshot_hash", sa.String(64), nullable=False, index=True),
        sa.Column("chapters_json", sa.JSON, server_default="[]"),
        sa.Column("character_profiles_json", sa.JSON, server_default="[]"),
        sa.Column("metadata_json", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )

    op.create_table(
        "storyboard_versions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("storyboard_project_id", sa.Integer, sa.ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("source_novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True, index=True),
        sa.Column("version_no", sa.Integer, nullable=False),
        sa.Column("parent_version_id", sa.Integer, sa.ForeignKey("storyboard_versions.id", ondelete="SET NULL"), nullable=True),
        sa.Column("lane", sa.String(32), nullable=False, server_default="vertical_feed"),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("is_default", sa.Integer, nullable=False, server_default="0"),
        sa.Column("is_final", sa.Integer, nullable=False, server_default="0"),
        sa.Column("quality_report_json", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_storyboard_versions_project_lane_version", "storyboard_versions", ["storyboard_project_id", "lane", "version_no"], unique=True)
    op.create_index("idx_storyboard_versions_project_default", "storyboard_versions", ["storyboard_project_id", "is_default"])

    op.create_table(
        "storyboard_runs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("public_id", sa.String(36), unique=True, index=True),
        sa.Column("storyboard_project_id", sa.Integer, sa.ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("requested_by_user_uuid", sa.String(36), nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("run_state", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("current_phase", sa.String(64), nullable=True),
        sa.Column("progress", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_category", sa.String(32), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("trace_id", sa.String(64), nullable=True, index=True),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_storyboard_runs_project_created", "storyboard_runs", ["storyboard_project_id", "created_at"])
    op.create_index("idx_storyboard_runs_project_status", "storyboard_runs", ["storyboard_project_id", "status"])

    op.create_table(
        "storyboard_run_lanes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("storyboard_run_id", sa.Integer, sa.ForeignKey("storyboard_runs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("storyboard_project_id", sa.Integer, sa.ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("lane", sa.String(32), nullable=False, index=True),
        sa.Column("storyboard_version_id", sa.Integer, sa.ForeignKey("storyboard_versions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("creation_task_public_id", sa.String(64), nullable=True, index=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("run_state", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("current_phase", sa.String(64), nullable=True),
        sa.Column("progress", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_category", sa.String(32), nullable=True),
        sa.Column("gate_report_json", sa.JSON, server_default="{}"),
        sa.Column("started_at", sa.DateTime, nullable=True),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_storyboard_run_lanes_run_lane", "storyboard_run_lanes", ["storyboard_run_id", "lane"], unique=True)
    op.create_index("idx_storyboard_run_lanes_project_status", "storyboard_run_lanes", ["storyboard_project_id", "status"])

    op.create_table(
        "storyboard_shots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("storyboard_version_id", sa.Integer, sa.ForeignKey("storyboard_versions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("episode_no", sa.Integer, nullable=False),
        sa.Column("scene_no", sa.Integer, nullable=False),
        sa.Column("shot_no", sa.Integer, nullable=False),
        sa.Column("location", sa.String(255), nullable=True),
        sa.Column("time_of_day", sa.String(32), nullable=True),
        sa.Column("shot_size", sa.String(50), nullable=True),
        sa.Column("camera_angle", sa.String(50), nullable=True),
        sa.Column("camera_move", sa.String(50), nullable=True),
        sa.Column("duration_sec", sa.Integer, nullable=False, server_default="3"),
        sa.Column("characters_json", sa.JSON, server_default="[]"),
        sa.Column("action", sa.Text, nullable=True),
        sa.Column("dialogue", sa.Text, nullable=True),
        sa.Column("emotion_beat", sa.String(255), nullable=True),
        sa.Column("transition", sa.String(50), nullable=True),
        sa.Column("sound_hint", sa.String(255), nullable=True),
        sa.Column("production_note", sa.Text, nullable=True),
        sa.Column("blocking", sa.Text, nullable=True),
        sa.Column("motivation", sa.String(255), nullable=True),
        sa.Column("performance_note", sa.Text, nullable=True),
        sa.Column("continuity_anchor", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_storyboard_shots_version_episode_scene_shot", "storyboard_shots", ["storyboard_version_id", "episode_no", "scene_no", "shot_no"], unique=True)

    op.create_table(
        "storyboard_character_cards",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("storyboard_project_id", sa.Integer, sa.ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("storyboard_version_id", sa.Integer, sa.ForeignKey("storyboard_versions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("lane", sa.String(32), nullable=False, server_default="vertical_feed"),
        sa.Column("character_key", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("skin_tone", sa.String(64), nullable=False),
        sa.Column("ethnicity", sa.String(64), nullable=False),
        sa.Column("master_prompt_text", sa.Text, nullable=False),
        sa.Column("negative_prompt_text", sa.Text, nullable=True),
        sa.Column("style_tags_json", sa.JSON, server_default="[]"),
        sa.Column("consistency_anchors_json", sa.JSON, server_default="[]"),
        sa.Column("quality_score", sa.Float, nullable=True),
        sa.Column("metadata_json", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_storyboard_character_cards_version_lane_character", "storyboard_character_cards", ["storyboard_version_id", "lane", "character_key"], unique=True)

    op.create_table(
        "storyboard_character_prompts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("storyboard_project_id", sa.Integer, sa.ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("storyboard_version_id", sa.Integer, sa.ForeignKey("storyboard_versions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("lane", sa.String(32), nullable=False, server_default="vertical_feed"),
        sa.Column("character_key", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("skin_tone", sa.String(64), nullable=False),
        sa.Column("ethnicity", sa.String(64), nullable=False),
        sa.Column("master_prompt_text", sa.Text, nullable=False),
        sa.Column("negative_prompt_text", sa.Text, nullable=True),
        sa.Column("style_tags_json", sa.JSON, server_default="[]"),
        sa.Column("consistency_anchors_json", sa.JSON, server_default="[]"),
        sa.Column("quality_score", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_storyboard_character_prompts_version_lane_character", "storyboard_character_prompts", ["storyboard_version_id", "lane", "character_key"], unique=True)

    op.create_table(
        "storyboard_gate_reports",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("storyboard_project_id", sa.Integer, sa.ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("storyboard_run_id", sa.Integer, sa.ForeignKey("storyboard_runs.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("storyboard_version_id", sa.Integer, sa.ForeignKey("storyboard_versions.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("gate_type", sa.String(32), nullable=False),
        sa.Column("gate_status", sa.String(32), nullable=False),
        sa.Column("missing_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("report_json", sa.JSON, server_default="{}"),
        sa.Column("created_by_user_uuid", sa.String(36), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_storyboard_gate_reports_project_type", "storyboard_gate_reports", ["storyboard_project_id", "gate_type"])

    op.create_table(
        "storyboard_exports",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("public_id", sa.String(36), unique=True, index=True),
        sa.Column("storyboard_project_id", sa.Integer, sa.ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("storyboard_version_id", sa.Integer, sa.ForeignKey("storyboard_versions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("requested_by_user_uuid", sa.String(36), nullable=False, index=True),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("content_type", sa.String(100), nullable=True),
        sa.Column("file_name", sa.String(255), nullable=True),
        sa.Column("storage_path", sa.Text, nullable=True),
        sa.Column("size_bytes", sa.Integer, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("finished_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_storyboard_exports_project_status", "storyboard_exports", ["storyboard_project_id", "status"])

    op.create_table(
        "storyboard_events_outbox",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("storyboard_project_id", sa.Integer, sa.ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("storyboard_run_id", sa.Integer, sa.ForeignKey("storyboard_runs.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("topic", sa.String(64), nullable=False, index=True),
        sa.Column("event_key", sa.String(128), nullable=False, index=True),
        sa.Column("payload_json", sa.JSON, server_default="{}"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_storyboard_outbox_topic_status", "storyboard_events_outbox", ["topic", "status"])

    op.create_table(
        "storyboard_audit_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("storyboard_project_id", sa.Integer, sa.ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("storyboard_run_id", sa.Integer, sa.ForeignKey("storyboard_runs.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("storyboard_version_id", sa.Integer, sa.ForeignKey("storyboard_versions.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("actor_user_uuid", sa.String(36), nullable=False, index=True),
        sa.Column("detail_json", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_storyboard_audit_project_action", "storyboard_audit_logs", ["storyboard_project_id", "action"])

    op.create_table(
        "storyboard_tasks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("storyboard_project_id", sa.Integer, sa.ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("task_id", sa.String(255), unique=True, nullable=False, index=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="submitted"),
        sa.Column("run_state", sa.String(32), nullable=False, server_default="submitted"),
        sa.Column("current_phase", sa.String(50), nullable=True),
        sa.Column("current_lane", sa.String(32), nullable=True),
        sa.Column("progress", sa.Float, nullable=False, server_default="0.0"),
        sa.Column("current_episode", sa.Integer, nullable=True),
        sa.Column("eta_seconds", sa.Integer, nullable=True),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("error_code", sa.String(100), nullable=True),
        sa.Column("error_category", sa.String(32), nullable=True),
        sa.Column("retryable", sa.Integer, nullable=False, server_default="0"),
        sa.Column("trace_id", sa.String(64), nullable=True, index=True),
        sa.Column("gate_report_json", sa.JSON, server_default="{}"),
        sa.Column("created_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_storyboard_tasks_project_status", "storyboard_tasks", ["storyboard_project_id", "status"])

    op.create_table(
        "storyboard_assertions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("storyboard_project_id", sa.Integer, sa.ForeignKey("storyboard_projects.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_uuid", sa.String(36), nullable=False, index=True),
        sa.Column("assertion_type", sa.String(50), nullable=False),
        sa.Column("assertion_text", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=True),
    )
    op.create_index("idx_storyboard_assertions_project_type", "storyboard_assertions", ["storyboard_project_id", "assertion_type"])


def downgrade() -> None:
    tables = [
        "storyboard_assertions", "storyboard_tasks", "storyboard_audit_logs",
        "storyboard_events_outbox", "storyboard_exports", "storyboard_gate_reports",
        "storyboard_character_prompts", "storyboard_character_cards", "storyboard_shots",
        "storyboard_run_lanes", "storyboard_runs", "storyboard_source_snapshots",
        "storyboard_versions", "storyboard_projects",
        "creation_task_checkpoints", "creation_tasks",
        "system_runtime_settings", "system_model_definitions", "system_model_providers",
        "admin_audit_logs", "password_reset_tokens", "email_verification_tokens",
        "usage_ledger", "user_quotas", "users",
        "rewrite_annotations", "rewrite_requests",
        "novel_feedback", "quality_reports",
        "generation_checkpoints", "story_snapshots",
        "story_foreshadows", "story_events", "story_facts", "story_entities",
        "chapter_annotations", "generation_tasks",
        "knowledge_chunks", "chapter_embeddings",
        "story_character_profiles", "novel_memory",
        "chapter_summaries", "novel_presets", "chapter_outlines",
        "novel_specifications", "chapter_versions",
        "novel_versions", "novels",
    ]
    for t in tables:
        op.drop_table(t)
    op.execute("DROP EXTENSION IF EXISTS vector")
