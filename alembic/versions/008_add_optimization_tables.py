"""add tables for optimization tickets #2/#4/#5/#6/#7/#12

Revision ID: 008
Revises: 007
Create Date: 2026-05-10 22:20:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- #2 alias_registry -------------------------------------------------
    op.create_table(
        "alias_registry",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("novel_version_id", sa.Integer(), sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("character_key", sa.String(64), nullable=False),
        sa.Column("alias", sa.String(255), nullable=False),
        sa.Column("alias_type", sa.String(32), nullable=False, server_default="surface"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("novel_version_id", "alias", name="uq_alias_registry_alias"),
    )
    op.create_index("idx_alias_registry_char", "alias_registry", ["novel_version_id", "character_key"])

    # --- #4 spacetime_anchors ---------------------------------------------
    op.create_table(
        "spacetime_anchors",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("novel_version_id", sa.Integer(), sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_num", sa.Integer(), nullable=False),
        sa.Column("when_text", sa.String(255), nullable=True),
        sa.Column("where_text", sa.String(255), nullable=True),
        sa.Column("who_keys", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=True),
        sa.Column("relative_to_prev", sa.String(16), nullable=True),
        sa.Column("payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("novel_version_id", "chapter_num", name="uq_spacetime_chapter"),
    )

    # --- #5 voice_fingerprints --------------------------------------------
    op.create_table(
        "voice_fingerprints",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("novel_version_id", sa.Integer(), sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("character_key", sa.String(64), nullable=False),
        sa.Column("avg_sentence_len", sa.Float(), nullable=False, server_default="0"),
        sa.Column("formality_score", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("register", sa.String(16), nullable=False, server_default="neutral"),
        sa.Column("sample_chapter_from", sa.Integer(), nullable=True),
        sa.Column("sample_chapter_to", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("novel_version_id", "character_key", name="uq_voice_fp_char"),
    )

    # --- #6 foreshadow lifecycle (extend story_foreshadows) --------------
    # 老表名: story_foreshadows；新增 lifecycle 字段（additive，全部 nullable）
    with op.batch_alter_table("story_foreshadows") as batch:
        batch.add_column(sa.Column("lifecycle_state", sa.String(16), nullable=True, server_default="planned"))
        batch.add_column(sa.Column("plant_chapter", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("payoff_chapter", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("plant_anchor", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("payoff_anchor", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("match_confidence", sa.Float(), nullable=True))
        batch.add_column(sa.Column("match_method", sa.String(16), nullable=True))

    # --- #7 outline_audit_reports -----------------------------------------
    op.create_table(
        "outline_audit_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("novel_version_id", sa.Integer(), sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_num", sa.Integer(), nullable=False),
        sa.Column("must_fix_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("partial_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("novel_version_id", "chapter_num", name="uq_outline_audit_chapter"),
    )

    # --- #12 reader_lens_reports -----------------------------------------
    op.create_table(
        "reader_lens_reports",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("novel_version_id", sa.Integer(), sa.ForeignKey("novel_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chapter_num", sa.Integer(), nullable=False),
        sa.Column("first_read_fluency", sa.Float(), nullable=False, server_default="0"),
        sa.Column("info_density", sa.Float(), nullable=False, server_default="0"),
        sa.Column("missing_setups", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("sampled_at_chapter", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("novel_version_id", "chapter_num", name="uq_reader_lens_chapter"),
    )


def downgrade() -> None:
    op.drop_table("reader_lens_reports")
    op.drop_table("outline_audit_reports")
    with op.batch_alter_table("story_foreshadows") as batch:
        for col in (
            "match_method",
            "match_confidence",
            "payoff_anchor",
            "plant_anchor",
            "payoff_chapter",
            "plant_chapter",
            "lifecycle_state",
        ):
            batch.drop_column(col)
    op.drop_table("voice_fingerprints")
    op.drop_table("spacetime_anchors")
    op.drop_index("idx_alias_registry_char", table_name="alias_registry")
    op.drop_table("alias_registry")
