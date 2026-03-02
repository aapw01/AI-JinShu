"""introduce version-scoped storage for novel generation and storyboard source binding

Revision ID: 017
Revises: 016
Create Date: 2026-03-01 19:40:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def _add_column_if_missing(table: str, column: sa.Column) -> None:
    inspector = sa.inspect(op.get_bind())
    cols = {c["name"] for c in inspector.get_columns(table)}
    if column.name not in cols:
        op.add_column(table, column)


def _drop_index_if_exists(index_name: str, table_name: str) -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {idx["name"] for idx in inspector.get_indexes(table_name)}
    if index_name in indexes:
        op.drop_index(index_name, table_name=table_name)


def _create_index_if_missing(name: str, table: str, cols: list[str], unique: bool = False) -> None:
    inspector = sa.inspect(op.get_bind())
    indexes = {idx["name"] for idx in inspector.get_indexes(table)}
    if name not in indexes:
        op.create_index(name, table, cols, unique=unique)


def upgrade() -> None:
    # Version scope columns
    _add_column_if_missing("chapter_outlines", sa.Column("novel_version_id", sa.Integer(), nullable=True))
    _add_column_if_missing("chapter_summaries", sa.Column("novel_version_id", sa.Integer(), nullable=True))
    _add_column_if_missing("novel_memory", sa.Column("novel_version_id", sa.Integer(), nullable=True))
    _add_column_if_missing("story_character_profiles", sa.Column("novel_version_id", sa.Integer(), nullable=True))
    _add_column_if_missing("chapter_embeddings", sa.Column("novel_version_id", sa.Integer(), nullable=True))
    _add_column_if_missing("knowledge_chunks", sa.Column("novel_version_id", sa.Integer(), nullable=True))
    _add_column_if_missing("story_entities", sa.Column("novel_version_id", sa.Integer(), nullable=True))
    _add_column_if_missing("story_facts", sa.Column("novel_version_id", sa.Integer(), nullable=True))
    _add_column_if_missing("story_events", sa.Column("novel_version_id", sa.Integer(), nullable=True))
    _add_column_if_missing("story_foreshadows", sa.Column("novel_version_id", sa.Integer(), nullable=True))
    _add_column_if_missing("story_snapshots", sa.Column("novel_version_id", sa.Integer(), nullable=True))
    _add_column_if_missing("quality_reports", sa.Column("novel_version_id", sa.Integer(), nullable=True))
    _add_column_if_missing("chapter_versions", sa.Column("review_score", sa.Float(), nullable=True))
    _add_column_if_missing("chapter_versions", sa.Column("language_quality_score", sa.Float(), nullable=True))
    _add_column_if_missing("chapter_versions", sa.Column("language_quality_report", sa.Text(), nullable=True))
    _add_column_if_missing("storyboard_versions", sa.Column("source_novel_version_id", sa.Integer(), nullable=True))

    # Ensure each novel has a default novel_version row.
    op.execute(
        sa.text(
            """
            INSERT INTO novel_versions (novel_id, version_no, status, is_default, created_at, updated_at)
            SELECT n.id, 1, 'completed', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            FROM novels n
            WHERE NOT EXISTS (
                SELECT 1 FROM novel_versions v WHERE v.novel_id = n.id
            )
            """
        )
    )

    # Ensure every novel has exactly one default flag set, prefer latest version_no.
    op.execute(
        sa.text(
            """
            WITH latest AS (
              SELECT novel_id, MAX(version_no) AS max_version_no
              FROM novel_versions
              GROUP BY novel_id
            )
            UPDATE novel_versions v
            SET is_default = CASE
              WHEN v.version_no = l.max_version_no THEN 1
              ELSE 0
            END
            FROM latest l
            WHERE v.novel_id = l.novel_id
            """
        )
    )

    # Backfill novel_version_id with default version id by novel.
    for table in (
        "chapter_outlines",
        "chapter_summaries",
        "novel_memory",
        "story_character_profiles",
        "chapter_embeddings",
        "knowledge_chunks",
        "story_entities",
        "story_facts",
        "story_events",
        "story_foreshadows",
        "story_snapshots",
        "quality_reports",
    ):
        op.execute(
            sa.text(
                f"""
                UPDATE {table} t
                SET novel_version_id = v.id
                FROM novel_versions v
                WHERE t.novel_id = v.novel_id
                  AND v.is_default = 1
                  AND t.novel_version_id IS NULL
                """
            )
        )

    # Backfill chapter version quality from chapters table.
    op.execute(
        sa.text(
            """
            UPDATE chapter_versions cv
            SET review_score = c.review_score,
                language_quality_score = c.language_quality_score,
                language_quality_report = c.language_quality_report
            FROM novel_versions nv
            JOIN chapters c
              ON c.novel_id = nv.novel_id
            WHERE cv.novel_version_id = nv.id
              AND nv.is_default = 1
              AND c.chapter_num = cv.chapter_num
            """
        )
    )

    # Bind storyboard versions to default novel version of its project.
    op.execute(
        sa.text(
            """
            UPDATE storyboard_versions sv
            SET source_novel_version_id = nv.id
            FROM storyboard_projects sp
            JOIN novel_versions nv
              ON nv.novel_id = sp.novel_id
             AND nv.is_default = 1
            WHERE sv.storyboard_project_id = sp.id
              AND sv.source_novel_version_id IS NULL
            """
        )
    )

    # Add FKs once columns are populated.
    op.create_foreign_key(
        "fk_chapter_outlines_novel_version_id",
        "chapter_outlines",
        "novel_versions",
        ["novel_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_chapter_summaries_novel_version_id",
        "chapter_summaries",
        "novel_versions",
        ["novel_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_novel_memory_novel_version_id",
        "novel_memory",
        "novel_versions",
        ["novel_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_story_character_profiles_novel_version_id",
        "story_character_profiles",
        "novel_versions",
        ["novel_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_chapter_embeddings_novel_version_id",
        "chapter_embeddings",
        "novel_versions",
        ["novel_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_knowledge_chunks_novel_version_id",
        "knowledge_chunks",
        "novel_versions",
        ["novel_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_story_entities_novel_version_id",
        "story_entities",
        "novel_versions",
        ["novel_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_story_facts_novel_version_id",
        "story_facts",
        "novel_versions",
        ["novel_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_story_events_novel_version_id",
        "story_events",
        "novel_versions",
        ["novel_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_story_foreshadows_novel_version_id",
        "story_foreshadows",
        "novel_versions",
        ["novel_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_story_snapshots_novel_version_id",
        "story_snapshots",
        "novel_versions",
        ["novel_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_quality_reports_novel_version_id",
        "quality_reports",
        "novel_versions",
        ["novel_version_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_storyboard_versions_source_novel_version_id",
        "storyboard_versions",
        "novel_versions",
        ["source_novel_version_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Rebuild indexes to version scope.
    _drop_index_if_exists("idx_chapter_summaries_novel_chapter", "chapter_summaries")
    _drop_index_if_exists("idx_novel_memory_novel_type_key", "novel_memory")
    _drop_index_if_exists("idx_story_entities_novel_type_name", "story_entities")
    _drop_index_if_exists("idx_story_facts_novel_entity_type", "story_facts")
    _drop_index_if_exists("idx_story_events_novel_chapter", "story_events")
    _drop_index_if_exists("idx_story_foreshadows_novel_state", "story_foreshadows")
    _drop_index_if_exists("idx_story_foreshadows_novel_foreshadow", "story_foreshadows")
    _drop_index_if_exists("idx_story_snapshots_novel_volume", "story_snapshots")
    _drop_index_if_exists("idx_quality_reports_novel_scope_scopeid", "quality_reports")
    _drop_index_if_exists("idx_story_character_profiles_novel_character", "story_character_profiles")

    _create_index_if_missing(
        "idx_chapter_summaries_novel_chapter",
        "chapter_summaries",
        ["novel_version_id", "chapter_num"],
        unique=True,
    )
    _create_index_if_missing(
        "idx_novel_memory_novel_type_key",
        "novel_memory",
        ["novel_version_id", "memory_type", "key"],
        unique=True,
    )
    _create_index_if_missing(
        "idx_story_entities_novel_type_name",
        "story_entities",
        ["novel_version_id", "entity_type", "name"],
        unique=True,
    )
    _create_index_if_missing(
        "idx_story_facts_novel_entity_type",
        "story_facts",
        ["novel_version_id", "entity_id", "fact_type"],
    )
    _create_index_if_missing(
        "idx_story_events_novel_chapter",
        "story_events",
        ["novel_version_id", "chapter_num"],
    )
    _create_index_if_missing(
        "idx_story_foreshadows_novel_state",
        "story_foreshadows",
        ["novel_version_id", "state"],
    )
    _create_index_if_missing(
        "idx_story_foreshadows_novel_foreshadow",
        "story_foreshadows",
        ["novel_version_id", "foreshadow_id"],
        unique=True,
    )
    _create_index_if_missing(
        "idx_story_snapshots_novel_volume",
        "story_snapshots",
        ["novel_version_id", "volume_no"],
    )
    _create_index_if_missing(
        "idx_quality_reports_novel_scope_scopeid",
        "quality_reports",
        ["novel_version_id", "scope", "scope_id"],
    )
    _create_index_if_missing(
        "idx_story_character_profiles_novel_character",
        "story_character_profiles",
        ["novel_version_id", "character_key"],
        unique=True,
    )
    _create_index_if_missing(
        "idx_chapter_outlines_version_chapter",
        "chapter_outlines",
        ["novel_version_id", "chapter_num"],
        unique=True,
    )
    _create_index_if_missing(
        "idx_chapter_embeddings_version_chapter",
        "chapter_embeddings",
        ["novel_version_id", "chapter_num"],
        unique=True,
    )
    _create_index_if_missing(
        "idx_knowledge_chunks_version_type",
        "knowledge_chunks",
        ["novel_version_id", "chunk_type"],
    )


def downgrade() -> None:
    # Indexes created in this revision.
    _drop_index_if_exists("idx_knowledge_chunks_version_type", "knowledge_chunks")
    _drop_index_if_exists("idx_chapter_embeddings_version_chapter", "chapter_embeddings")
    _drop_index_if_exists("idx_chapter_outlines_version_chapter", "chapter_outlines")
    _drop_index_if_exists("idx_story_character_profiles_novel_character", "story_character_profiles")
    _drop_index_if_exists("idx_quality_reports_novel_scope_scopeid", "quality_reports")
    _drop_index_if_exists("idx_story_snapshots_novel_volume", "story_snapshots")
    _drop_index_if_exists("idx_story_foreshadows_novel_foreshadow", "story_foreshadows")
    _drop_index_if_exists("idx_story_foreshadows_novel_state", "story_foreshadows")
    _drop_index_if_exists("idx_story_events_novel_chapter", "story_events")
    _drop_index_if_exists("idx_story_facts_novel_entity_type", "story_facts")
    _drop_index_if_exists("idx_story_entities_novel_type_name", "story_entities")
    _drop_index_if_exists("idx_novel_memory_novel_type_key", "novel_memory")
    _drop_index_if_exists("idx_chapter_summaries_novel_chapter", "chapter_summaries")

    # Best-effort restore legacy index definitions.
    _create_index_if_missing("idx_chapter_summaries_novel_chapter", "chapter_summaries", ["novel_id", "chapter_num"], unique=True)
    _create_index_if_missing("idx_novel_memory_novel_type_key", "novel_memory", ["novel_id", "memory_type", "key"], unique=True)
    _create_index_if_missing("idx_story_entities_novel_type_name", "story_entities", ["novel_id", "entity_type", "name"], unique=True)
    _create_index_if_missing("idx_story_facts_novel_entity_type", "story_facts", ["novel_id", "entity_id", "fact_type"])
    _create_index_if_missing("idx_story_events_novel_chapter", "story_events", ["novel_id", "chapter_num"])
    _create_index_if_missing("idx_story_foreshadows_novel_state", "story_foreshadows", ["novel_id", "state"])
    _create_index_if_missing("idx_story_foreshadows_novel_foreshadow", "story_foreshadows", ["novel_id", "foreshadow_id"], unique=True)
    _create_index_if_missing("idx_story_snapshots_novel_volume", "story_snapshots", ["novel_id", "volume_no"])
    _create_index_if_missing("idx_quality_reports_novel_scope_scopeid", "quality_reports", ["novel_id", "scope", "scope_id"])
    _create_index_if_missing("idx_story_character_profiles_novel_character", "story_character_profiles", ["novel_id", "character_key"], unique=True)

    # Drop FKs/columns.
    for fk_name, table in (
        ("fk_storyboard_versions_source_novel_version_id", "storyboard_versions"),
        ("fk_quality_reports_novel_version_id", "quality_reports"),
        ("fk_story_snapshots_novel_version_id", "story_snapshots"),
        ("fk_story_foreshadows_novel_version_id", "story_foreshadows"),
        ("fk_story_events_novel_version_id", "story_events"),
        ("fk_story_facts_novel_version_id", "story_facts"),
        ("fk_story_entities_novel_version_id", "story_entities"),
        ("fk_knowledge_chunks_novel_version_id", "knowledge_chunks"),
        ("fk_chapter_embeddings_novel_version_id", "chapter_embeddings"),
        ("fk_story_character_profiles_novel_version_id", "story_character_profiles"),
        ("fk_novel_memory_novel_version_id", "novel_memory"),
        ("fk_chapter_summaries_novel_version_id", "chapter_summaries"),
        ("fk_chapter_outlines_novel_version_id", "chapter_outlines"),
    ):
        try:
            op.drop_constraint(fk_name, table, type_="foreignkey")
        except Exception:
            pass

    for table, col in (
        ("storyboard_versions", "source_novel_version_id"),
        ("chapter_versions", "language_quality_report"),
        ("chapter_versions", "language_quality_score"),
        ("chapter_versions", "review_score"),
        ("quality_reports", "novel_version_id"),
        ("story_snapshots", "novel_version_id"),
        ("story_foreshadows", "novel_version_id"),
        ("story_events", "novel_version_id"),
        ("story_facts", "novel_version_id"),
        ("story_entities", "novel_version_id"),
        ("knowledge_chunks", "novel_version_id"),
        ("chapter_embeddings", "novel_version_id"),
        ("story_character_profiles", "novel_version_id"),
        ("novel_memory", "novel_version_id"),
        ("chapter_summaries", "novel_version_id"),
        ("chapter_outlines", "novel_version_id"),
    ):
        try:
            op.drop_column(table, col)
        except Exception:
            pass
