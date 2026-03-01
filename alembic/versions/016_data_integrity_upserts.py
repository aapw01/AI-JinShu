"""add unique indexes for upsert-safe memory/checkpoint flows

Revision ID: 016
Revises: 015
Create Date: 2026-02-28 17:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None


def _dedupe_by_keys(table: str, keys: list[str]) -> None:
    key_expr = ", ".join(keys)
    op.execute(
        sa.text(
            f"""
            DELETE FROM {table}
            WHERE id IN (
                SELECT id FROM (
                    SELECT id,
                           ROW_NUMBER() OVER (PARTITION BY {key_expr} ORDER BY id DESC) AS rn
                    FROM {table}
                ) t
                WHERE t.rn > 1
            )
            """
        )
    )


def upgrade() -> None:
    _dedupe_by_keys("novel_specifications", ["novel_id", "spec_type"])
    _dedupe_by_keys("chapter_summaries", ["novel_id", "chapter_num"])
    _dedupe_by_keys("novel_memory", ["novel_id", "memory_type", '"key"'])
    _dedupe_by_keys("story_entities", ["novel_id", "entity_type", "name"])
    _dedupe_by_keys("story_foreshadows", ["novel_id", "foreshadow_id"])

    op.create_index(
        "idx_novel_specifications_novel_type",
        "novel_specifications",
        ["novel_id", "spec_type"],
        unique=True,
    )
    op.create_index(
        "idx_chapter_summaries_novel_chapter",
        "chapter_summaries",
        ["novel_id", "chapter_num"],
        unique=True,
    )
    op.create_index(
        "idx_novel_memory_novel_type_key",
        "novel_memory",
        ["novel_id", "memory_type", "key"],
        unique=True,
    )

    op.drop_index("idx_story_entities_novel_type_name", table_name="story_entities")
    op.create_index(
        "idx_story_entities_novel_type_name",
        "story_entities",
        ["novel_id", "entity_type", "name"],
        unique=True,
    )
    op.create_index(
        "idx_story_foreshadows_novel_foreshadow",
        "story_foreshadows",
        ["novel_id", "foreshadow_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_story_foreshadows_novel_foreshadow", table_name="story_foreshadows")

    op.drop_index("idx_story_entities_novel_type_name", table_name="story_entities")
    op.create_index(
        "idx_story_entities_novel_type_name",
        "story_entities",
        ["novel_id", "entity_type", "name"],
        unique=False,
    )

    op.drop_index("idx_novel_memory_novel_type_key", table_name="novel_memory")
    op.drop_index("idx_chapter_summaries_novel_chapter", table_name="chapter_summaries")
    op.drop_index("idx_novel_specifications_novel_type", table_name="novel_specifications")
