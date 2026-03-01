"""Add generation task state governance fields.

Revision ID: 009
Revises: 008
Create Date: 2026-02-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("generation_tasks", sa.Column("run_state", sa.String(length=32), nullable=True, server_default="submitted"))
    op.add_column("generation_tasks", sa.Column("error_code", sa.String(length=100), nullable=True))
    op.add_column("generation_tasks", sa.Column("error_category", sa.String(length=32), nullable=True))
    op.add_column("generation_tasks", sa.Column("retryable", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("generation_tasks", sa.Column("idempotency_key", sa.String(length=128), nullable=True))
    op.create_index("ix_generation_tasks_idempotency_key", "generation_tasks", ["idempotency_key"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_generation_tasks_idempotency_key", table_name="generation_tasks")
    op.drop_column("generation_tasks", "idempotency_key")
    op.drop_column("generation_tasks", "retryable")
    op.drop_column("generation_tasks", "error_category")
    op.drop_column("generation_tasks", "error_code")
    op.drop_column("generation_tasks", "run_state")
