"""expand story_facts (#9) + add fact_extraction_failures (#11)

Revision ID: 007
Revises: 006
Create Date: 2026-05-10 22:10:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- #9: story_facts expand (additive only) ---------------------------
    with op.batch_alter_table("story_facts") as batch:
        batch.add_column(sa.Column("source_chapter", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("source_run_id", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column("source_kind", sa.String(32), nullable=True, server_default="extractor")
        )
        batch.add_column(sa.Column("confidence", sa.Float(), nullable=True, server_default="0.5"))
        batch.add_column(sa.Column("extractor_model", sa.String(128), nullable=True))
        batch.add_column(sa.Column("verified_chapter", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("superseded_by", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=True,
                server_default=sa.text("true"),
            )
        )
    op.create_index(
        "idx_story_facts_entity_active",
        "story_facts",
        ["entity_id", "is_active"],
    )

    # --- #11: fact_extraction_failures ------------------------------------
    op.create_table(
        "fact_extraction_failures",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer(), sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "novel_version_id",
            sa.Integer(),
            sa.ForeignKey("novel_versions.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("chapter_num", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("failure_kind", sa.String(32), nullable=False),
        sa.Column("error_payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("novel_version_id", "chapter_num", "run_id", name="uq_fact_failures_run"),
    )
    op.create_index(
        "idx_fact_extraction_failures_status",
        "fact_extraction_failures",
        ["status", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_fact_extraction_failures_status", table_name="fact_extraction_failures")
    op.drop_table("fact_extraction_failures")
    op.drop_index("idx_story_facts_entity_active", table_name="story_facts")
    with op.batch_alter_table("story_facts") as batch:
        for col in (
            "is_active",
            "superseded_by",
            "verified_chapter",
            "extractor_model",
            "confidence",
            "source_kind",
            "source_run_id",
            "source_chapter",
        ):
            batch.drop_column(col)
