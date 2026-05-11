"""add cv_promotion_state table (Phase 0 §4.7)

Revision ID: 006
Revises: 005
Create Date: 2026-05-10 22:05:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cv_promotion_state",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("flag_name", sa.String(128), nullable=False, unique=True),
        sa.Column("phase", sa.String(32), nullable=False),  # baseline|canary_10|canary_50|full|rolled_back
        sa.Column("baseline_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("current_canary_pct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_check_at", sa.DateTime(timezone=False), nullable=True),
        sa.Column("verdict", sa.String(32), nullable=True),
        sa.Column("payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=False),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=False),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_cv_promotion_state_phase",
        "cv_promotion_state",
        ["phase", "last_check_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_cv_promotion_state_phase", table_name="cv_promotion_state")
    op.drop_table("cv_promotion_state")
