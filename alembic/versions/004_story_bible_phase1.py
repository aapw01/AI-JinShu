"""Phase 1 story bible and checkpoint tables.

Revision ID: 004
Revises: 003
Create Date: 2026-02-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "story_entities",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=True, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_story_entities_novel_type_name", "story_entities", ["novel_id", "entity_type", "name"], unique=False)

    op.create_table(
        "story_facts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("fact_type", sa.String(length=100), nullable=False),
        sa.Column("value_json", sa.JSON(), nullable=True),
        sa.Column("chapter_from", sa.Integer(), nullable=False),
        sa.Column("chapter_to", sa.Integer(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=True, server_default="1"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["entity_id"], ["story_entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_story_facts_novel_entity_type", "story_facts", ["novel_id", "entity_id", "fact_type"], unique=False)

    op.create_table(
        "story_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("chapter_num", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=True),
        sa.Column("actors", sa.JSON(), nullable=True),
        sa.Column("causes", sa.JSON(), nullable=True),
        sa.Column("effects", sa.JSON(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_story_events_novel_chapter", "story_events", ["novel_id", "chapter_num"], unique=False)
    op.create_index(op.f("ix_story_events_event_id"), "story_events", ["event_id"], unique=False)

    op.create_table(
        "story_foreshadows",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("foreshadow_id", sa.String(length=64), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("planted_chapter", sa.Integer(), nullable=False),
        sa.Column("resolved_chapter", sa.Integer(), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=True, server_default="planted"),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_story_foreshadows_novel_state", "story_foreshadows", ["novel_id", "state"], unique=False)
    op.create_index(op.f("ix_story_foreshadows_foreshadow_id"), "story_foreshadows", ["foreshadow_id"], unique=False)

    op.create_table(
        "story_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("volume_no", sa.Integer(), nullable=False),
        sa.Column("chapter_end", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_story_snapshots_novel_volume", "story_snapshots", ["novel_id", "volume_no"], unique=False)

    op.create_table(
        "generation_checkpoints",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("volume_no", sa.Integer(), nullable=False),
        sa.Column("chapter_num", sa.Integer(), nullable=False),
        sa.Column("node", sa.String(length=100), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_generation_checkpoints_task_node", "generation_checkpoints", ["task_id", "node"], unique=False)
    op.create_index(op.f("ix_generation_checkpoints_task_id"), "generation_checkpoints", ["task_id"], unique=False)

    op.create_table(
        "quality_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=20), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=True),
        sa.Column("verdict", sa.String(length=32), nullable=False, server_default="unknown"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_quality_reports_novel_scope_scopeid", "quality_reports", ["novel_id", "scope", "scope_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_quality_reports_novel_scope_scopeid", table_name="quality_reports")
    op.drop_table("quality_reports")

    op.drop_index(op.f("ix_generation_checkpoints_task_id"), table_name="generation_checkpoints")
    op.drop_index("idx_generation_checkpoints_task_node", table_name="generation_checkpoints")
    op.drop_table("generation_checkpoints")

    op.drop_index("idx_story_snapshots_novel_volume", table_name="story_snapshots")
    op.drop_table("story_snapshots")

    op.drop_index(op.f("ix_story_foreshadows_foreshadow_id"), table_name="story_foreshadows")
    op.drop_index("idx_story_foreshadows_novel_state", table_name="story_foreshadows")
    op.drop_table("story_foreshadows")

    op.drop_index(op.f("ix_story_events_event_id"), table_name="story_events")
    op.drop_index("idx_story_events_novel_chapter", table_name="story_events")
    op.drop_table("story_events")

    op.drop_index("idx_story_facts_novel_entity_type", table_name="story_facts")
    op.drop_table("story_facts")

    op.drop_index("idx_story_entities_novel_type_name", table_name="story_entities")
    op.drop_table("story_entities")
