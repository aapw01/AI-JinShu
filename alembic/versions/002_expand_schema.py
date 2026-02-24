"""Expand schema - new tables and columns.

Revision ID: 002
Revises: 001
Create Date: 2025-02-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    import uuid
    op.add_column("novels", sa.Column("uuid", sa.String(36), nullable=True))
    conn = op.get_bind()
    for row in conn.execute(sa.text("SELECT id FROM novels WHERE uuid IS NULL")):
        conn.execute(sa.text("UPDATE novels SET uuid = :u WHERE id = :id"), {"u": str(uuid.uuid4()), "id": row[0]})
    op.add_column("novels", sa.Column("user_id", sa.String(255), nullable=True))
    op.add_column("novels", sa.Column("target_language", sa.String(20), nullable=True))
    op.execute("UPDATE novels SET target_language = language WHERE target_language IS NULL AND language IS NOT NULL")
    op.add_column("novels", sa.Column("native_style_profile", sa.Text(), nullable=True))
    op.add_column("novels", sa.Column("pace", sa.String(50), nullable=True))
    op.add_column("novels", sa.Column("audience", sa.String(100), nullable=True))
    op.add_column("novels", sa.Column("target_length", sa.String(50), nullable=True))
    op.add_column("novels", sa.Column("writing_method", sa.String(100), nullable=True))
    op.add_column("novels", sa.Column("strategy", sa.String(100), nullable=True))
    op.add_column("novels", sa.Column("user_idea", sa.Text(), nullable=True))
    op.add_column("novels", sa.Column("inspiration_tags", sa.JSON(), nullable=True))
    op.create_index("ix_novels_uuid", "novels", ["uuid"], unique=True)
    op.create_index("ix_novels_user_id", "novels", ["user_id"], unique=False)

    op.add_column("chapters", sa.Column("review_score", sa.Float(), nullable=True))
    op.add_column("chapters", sa.Column("language_quality_score", sa.Float(), nullable=True))

    op.create_table(
        "novel_specifications",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("spec_type", sa.String(50), nullable=False),
        sa.Column("content", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "chapter_outlines",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("chapter_num", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(255), nullable=True),
        sa.Column("outline", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "novel_memory",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("memory_type", sa.String(50), nullable=False),
        sa.Column("key", sa.String(255), nullable=True),
        sa.Column("content", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "chapter_embeddings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("chapter_num", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute("ALTER TABLE chapter_embeddings ADD COLUMN embedding vector(1536)")

    op.create_table(
        "generation_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.String(255), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(50), nullable=True),
        sa.Column("step", sa.String(50), nullable=True),
        sa.Column("current_chapter", sa.Integer(), nullable=True),
        sa.Column("progress", sa.Float(), nullable=True),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("num_chapters", sa.Integer(), nullable=True),
        sa.Column("start_chapter", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_generation_tasks_task_id", "generation_tasks", ["task_id"], unique=True)

    op.create_table(
        "chapter_annotations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("chapter_num", sa.Integer(), nullable=False),
        sa.Column("annotation_type", sa.String(50), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=True),
        sa.Column("end_offset", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("chapter_annotations")
    op.drop_table("generation_tasks")
    op.drop_table("chapter_embeddings")
    op.drop_table("novel_memory")
    op.drop_table("chapter_outlines")
    op.drop_table("novel_specifications")
    op.drop_column("chapters", "language_quality_score")
    op.drop_column("chapters", "review_score")
    op.drop_index("ix_novels_user_id", "novels")
    op.drop_index("ix_novels_uuid", "novels")
    op.drop_column("novels", "inspiration_tags")
    op.drop_column("novels", "user_idea")
    op.drop_column("novels", "strategy")
    op.drop_column("novels", "writing_method")
    op.drop_column("novels", "target_length")
    op.drop_column("novels", "audience")
    op.drop_column("novels", "pace")
    op.drop_column("novels", "native_style_profile")
    op.drop_column("novels", "target_language")
    op.drop_column("novels", "user_id")
    op.drop_column("novels", "uuid")
