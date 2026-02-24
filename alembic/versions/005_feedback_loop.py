"""Add feedback loop table.

Revision ID: 005
Revises: 004
Create Date: 2026-02-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "novel_feedback",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("chapter_num", sa.Integer(), nullable=True),
        sa.Column("volume_no", sa.Integer(), nullable=True),
        sa.Column("feedback_type", sa.String(length=32), nullable=False, server_default="editor"),
        sa.Column("rating", sa.Float(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_novel_feedback_novel_chapter_volume",
        "novel_feedback",
        ["novel_id", "chapter_num", "volume_no"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_novel_feedback_novel_chapter_volume", table_name="novel_feedback")
    op.drop_table("novel_feedback")
