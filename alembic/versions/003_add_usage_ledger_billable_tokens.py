"""add usage ledger billable tokens

Revision ID: 003
Revises: b71194aec847
Create Date: 2026-04-29 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "003"
down_revision = "b71194aec847"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "usage_ledger",
        sa.Column("billable_tokens", sa.Integer(), nullable=False, server_default="0"),
    )
    op.execute(
        "UPDATE usage_ledger SET billable_tokens = COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)"
    )


def downgrade() -> None:
    op.drop_column("usage_ledger", "billable_tokens")
