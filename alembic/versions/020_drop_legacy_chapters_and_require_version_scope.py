"""drop legacy chapters table and enforce version-scoped non-null references

Revision ID: 020
Revises: 019
Create Date: 2026-03-05 12:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None

_VERSION_SCOPED_TABLES = (
    "chapter_outlines",
    "chapter_summaries",
    "novel_memory",
    "chapter_embeddings",
    "quality_reports",
    "story_snapshots",
    "story_entities",
    "story_events",
    "story_foreshadows",
    "story_character_profiles",
    "knowledge_chunks",
)


def _inspector() -> sa.Inspector:
    return sa.inspect(op.get_bind())


def _table_exists(table_name: str) -> bool:
    return table_name in set(_inspector().get_table_names())


def _column_exists(table_name: str, column_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return column_name in {col["name"] for col in _inspector().get_columns(table_name)}


def _index_exists(table_name: str, index_name: str) -> bool:
    if not _table_exists(table_name):
        return False
    return index_name in {idx["name"] for idx in _inspector().get_indexes(table_name)}


def _ensure_one_default_version_per_novel() -> None:
    bind = op.get_bind()
    novel_ids = [int(row[0]) for row in bind.execute(sa.text("SELECT id FROM novels")).fetchall()]
    for novel_id in novel_ids:
        rows = bind.execute(
            sa.text(
                """
                SELECT id
                FROM novel_versions
                WHERE novel_id = :novel_id
                ORDER BY is_default DESC, version_no DESC, id DESC
                """
            ),
            {"novel_id": novel_id},
        ).fetchall()
        if not rows:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO novel_versions (novel_id, version_no, status, is_default, created_at, updated_at)
                    VALUES (:novel_id, 1, 'draft', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """
                ),
                {"novel_id": novel_id},
            )
            continue
        keep_id = int(rows[0][0])
        bind.execute(
            sa.text(
                """
                UPDATE novel_versions
                SET is_default = CASE WHEN id = :keep_id THEN 1 ELSE 0 END
                WHERE novel_id = :novel_id
                """
            ),
            {"novel_id": novel_id, "keep_id": keep_id},
        )


def _backfill_version_scope(table_name: str) -> None:
    if not _column_exists(table_name, "novel_version_id"):
        return
    bind = op.get_bind()
    bind.execute(
        sa.text(
            f"""
            UPDATE {table_name}
            SET novel_version_id = (
                SELECT id
                FROM novel_versions v
                WHERE v.novel_id = {table_name}.novel_id
                  AND v.is_default = 1
                ORDER BY v.id ASC
                LIMIT 1
            )
            WHERE novel_version_id IS NULL
            """
        )
    )


def _set_version_scope_nullable(nullable: bool) -> None:
    for table_name in _VERSION_SCOPED_TABLES:
        if not _column_exists(table_name, "novel_version_id"):
            continue
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.alter_column(
                "novel_version_id",
                existing_type=sa.Integer(),
                nullable=nullable,
            )


def upgrade() -> None:
    _ensure_one_default_version_per_novel()
    for table_name in _VERSION_SCOPED_TABLES:
        _backfill_version_scope(table_name)
    _set_version_scope_nullable(nullable=False)

    if not _index_exists("novel_versions", "uq_novel_versions_one_default"):
        op.create_index(
            "uq_novel_versions_one_default",
            "novel_versions",
            ["novel_id"],
            unique=True,
            postgresql_where=sa.text("is_default = 1"),
            sqlite_where=sa.text("is_default = 1"),
        )

    if _table_exists("chapters"):
        op.drop_table("chapters")


def downgrade() -> None:
    if not _table_exists("chapters"):
        op.create_table(
            "chapters",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("novel_id", sa.Integer(), sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
            sa.Column("chapter_num", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=True),
            sa.Column("content", sa.Text(), nullable=True),
            sa.Column("summary", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=50), nullable=True, server_default="pending"),
            sa.Column("review_score", sa.Float(), nullable=True),
            sa.Column("language_quality_score", sa.Float(), nullable=True),
            sa.Column("language_quality_report", sa.Text(), nullable=True),
            sa.Column("metadata", sa.JSON(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    if _index_exists("novel_versions", "uq_novel_versions_one_default"):
        op.drop_index("uq_novel_versions_one_default", table_name="novel_versions")

    _set_version_scope_nullable(nullable=True)
