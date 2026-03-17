"""add novel memory revisions audit table

Revision ID: 002
Revises: 001
Create Date: 2026-03-17 10:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "novel_memory_revisions",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer, sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_version_id", sa.Integer, sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("memory_type", sa.String(length=50), nullable=False),
        sa.Column("memory_key", sa.String(length=255), nullable=True),
        sa.Column("source_chapter_num", sa.Integer, nullable=True),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("old_content", sa.JSON(), nullable=True, server_default="{}"),
        sa.Column("new_content", sa.JSON(), nullable=True, server_default="{}"),
        sa.Column("promotion_score", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index("idx_novel_memory_revisions_novel", "novel_memory_revisions", ["novel_id"])
    op.create_index("idx_novel_memory_revisions_version", "novel_memory_revisions", ["novel_version_id"])
    op.create_index("idx_novel_memory_revisions_type", "novel_memory_revisions", ["memory_type"])
    op.create_index("idx_novel_memory_revisions_key", "novel_memory_revisions", ["memory_key"])
    op.create_index("idx_novel_memory_revisions_source_chapter", "novel_memory_revisions", ["source_chapter_num"])


def downgrade() -> None:
    op.drop_index("idx_novel_memory_revisions_source_chapter", table_name="novel_memory_revisions")
    op.drop_index("idx_novel_memory_revisions_key", table_name="novel_memory_revisions")
    op.drop_index("idx_novel_memory_revisions_type", table_name="novel_memory_revisions")
    op.drop_index("idx_novel_memory_revisions_version", table_name="novel_memory_revisions")
    op.drop_index("idx_novel_memory_revisions_novel", table_name="novel_memory_revisions")
    op.drop_table("novel_memory_revisions")
