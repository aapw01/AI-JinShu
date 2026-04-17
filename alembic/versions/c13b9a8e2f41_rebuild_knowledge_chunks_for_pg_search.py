"""rebuild knowledge_chunks for pg search

Revision ID: c13b9a8e2f41
Revises: b71194aec847
Create Date: 2026-04-17 23:40:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c13b9a8e2f41"
down_revision = "b71194aec847"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.drop_index("idx_knowledge_chunks_version_type", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer(), sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_version_id", sa.Integer(), sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("source_key", sa.String(length=255), nullable=False),
        sa.Column("chapter_num", sa.Integer(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True),
        sa.Column("importance_score", sa.Float(), nullable=False, server_default="0"),
        sa.Column("chunk_type", sa.String(length=50), nullable=True),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.execute("ALTER TABLE knowledge_chunks ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector(1536)")
    op.create_index("idx_knowledge_chunks_version_type", "knowledge_chunks", ["novel_version_id", "chunk_type"])
    op.create_index(
        "idx_knowledge_chunks_scope_source_key",
        "knowledge_chunks",
        ["novel_id", sa.text("coalesce(novel_version_id, 0)"), "source_key"],
        unique=True,
    )
    op.create_index("idx_knowledge_chunks_version_source_type", "knowledge_chunks", ["novel_version_id", "source_type"])
    op.create_index("idx_knowledge_chunks_version_chapter", "knowledge_chunks", ["novel_version_id", "chapter_num"])
    op.create_index(
        "idx_knowledge_chunks_search_vector",
        "knowledge_chunks",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index(
        "idx_knowledge_chunks_search_text_trgm",
        "knowledge_chunks",
        ["search_text"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"search_text": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("idx_knowledge_chunks_search_text_trgm", table_name="knowledge_chunks")
    op.drop_index("idx_knowledge_chunks_search_vector", table_name="knowledge_chunks")
    op.drop_index("idx_knowledge_chunks_version_chapter", table_name="knowledge_chunks")
    op.drop_index("idx_knowledge_chunks_version_source_type", table_name="knowledge_chunks")
    op.drop_index("idx_knowledge_chunks_scope_source_key", table_name="knowledge_chunks")
    op.drop_index("idx_knowledge_chunks_version_type", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")

    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer(), sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("novel_version_id", sa.Integer(), sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("chunk_type", sa.String(length=50), nullable=True),
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.execute("ALTER TABLE knowledge_chunks ALTER COLUMN embedding TYPE vector(1536) USING embedding::vector(1536)")
    op.create_index("idx_knowledge_chunks_version_type", "knowledge_chunks", ["novel_version_id", "chunk_type"])
