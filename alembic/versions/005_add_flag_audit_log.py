"""add flag_audit_log table (Phase 0 / 4.2.1 in agent-engineering-roadmap)

Revision ID: 005
Revises: 004
Create Date: 2026-05-10 21:55:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flag_audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("flag_name", sa.String(128), nullable=False),
        sa.Column("changed_by", sa.String(128), nullable=False),
        sa.Column("before_state", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("after_state", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=False),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_flag_audit_log_flag_time",
        "flag_audit_log",
        ["flag_name", "changed_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_flag_audit_log_flag_time", table_name="flag_audit_log")
    op.drop_table("flag_audit_log")
