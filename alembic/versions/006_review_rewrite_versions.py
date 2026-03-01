"""Add novel versioning and rewrite workflow tables.

Revision ID: 006
Revises: 005
Create Date: 2026-02-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "novel_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", sa.Integer(), nullable=True),
        sa.Column("source_task_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("is_default", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["parent_version_id"], ["novel_versions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_novel_versions_novel_version", "novel_versions", ["novel_id", "version_no"], unique=True)
    op.create_index("idx_novel_versions_novel_default", "novel_versions", ["novel_id", "is_default"], unique=False)
    op.create_index("ix_novel_versions_source_task_id", "novel_versions", ["source_task_id"], unique=False)

    op.create_table(
        "chapter_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("novel_version_id", sa.Integer(), nullable=False),
        sa.Column("chapter_num", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("source_chapter_version_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_version_id"], ["novel_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_chapter_version_id"], ["chapter_versions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_chapter_versions_version_chapter", "chapter_versions", ["novel_version_id", "chapter_num"], unique=True)

    op.create_table(
        "rewrite_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("base_version_id", sa.Integer(), nullable=False),
        sa.Column("target_version_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="submitted"),
        sa.Column("rewrite_from_chapter", sa.Integer(), nullable=False),
        sa.Column("rewrite_to_chapter", sa.Integer(), nullable=False),
        sa.Column("current_chapter", sa.Integer(), nullable=True),
        sa.Column("progress", sa.Float(), nullable=True, server_default="0"),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["base_version_id"], ["novel_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_version_id"], ["novel_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_rewrite_requests_novel_status", "rewrite_requests", ["novel_id", "status"], unique=False)
    op.create_index("ix_rewrite_requests_task_id", "rewrite_requests", ["task_id"], unique=False)

    op.create_table(
        "rewrite_annotations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rewrite_request_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("base_version_id", sa.Integer(), nullable=False),
        sa.Column("chapter_num", sa.Integer(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=True),
        sa.Column("end_offset", sa.Integer(), nullable=True),
        sa.Column("selected_text", sa.Text(), nullable=True),
        sa.Column("issue_type", sa.String(length=32), nullable=False, server_default="other"),
        sa.Column("instruction", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(length=16), nullable=False, server_default="should"),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["rewrite_request_id"], ["rewrite_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["base_version_id"], ["novel_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_rewrite_annotations_request_chapter", "rewrite_annotations", ["rewrite_request_id", "chapter_num"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_rewrite_annotations_request_chapter", table_name="rewrite_annotations")
    op.drop_table("rewrite_annotations")
    op.drop_index("ix_rewrite_requests_task_id", table_name="rewrite_requests")
    op.drop_index("idx_rewrite_requests_novel_status", table_name="rewrite_requests")
    op.drop_table("rewrite_requests")
    op.drop_index("idx_chapter_versions_version_chapter", table_name="chapter_versions")
    op.drop_table("chapter_versions")
    op.drop_index("ix_novel_versions_source_task_id", table_name="novel_versions")
    op.drop_index("idx_novel_versions_novel_default", table_name="novel_versions")
    op.drop_index("idx_novel_versions_novel_version", table_name="novel_versions")
    op.drop_table("novel_versions")
