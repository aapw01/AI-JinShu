"""add agent_events table (Phase 0 / 4.1 in agent-engineering-roadmap)

Revision ID: 004
Revises: 003
Create Date: 2026-05-10 21:50:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column(
            "novel_id",
            sa.Integer(),
            sa.ForeignKey("novels.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "novel_version_id",
            sa.Integer(),
            sa.ForeignKey("novel_versions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("task_id", sa.String(255), nullable=True),
        sa.Column("chapter_num", sa.Integer(), nullable=True),
        sa.Column("agent_name", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_category", sa.String(32), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_agent_events_novel_chapter_time",
        "agent_events",
        ["novel_id", "chapter_num", "created_at"],
    )
    op.create_index(
        "idx_agent_events_agent_event_time",
        "agent_events",
        ["agent_name", "event_type", "created_at"],
    )
    op.create_index("idx_agent_events_trace", "agent_events", ["trace_id"])
    op.create_index("idx_agent_events_task", "agent_events", ["task_id"])


def downgrade() -> None:
    op.drop_index("idx_agent_events_task", table_name="agent_events")
    op.drop_index("idx_agent_events_trace", table_name="agent_events")
    op.drop_index("idx_agent_events_agent_event_time", table_name="agent_events")
    op.drop_index("idx_agent_events_novel_chapter_time", table_name="agent_events")
    op.drop_table("agent_events")
