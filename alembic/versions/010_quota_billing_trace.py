"""add quota and billing tables plus trace id

Revision ID: 010
Revises: 009
Create Date: 2026-02-27 12:00:00.000000
"""
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("generation_tasks", sa.Column("trace_id", sa.String(length=64), nullable=True))
    op.create_index("ix_generation_tasks_trace_id", "generation_tasks", ["trace_id"], unique=False)

    op.create_table(
        "user_quotas",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("plan_key", sa.String(length=32), nullable=False, server_default="free"),
        sa.Column("max_concurrent_tasks", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("monthly_chapter_limit", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("monthly_token_limit", sa.Integer(), nullable=False, server_default="1000000"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("idx_user_quotas_user_plan", "user_quotas", ["user_id", "plan_key"], unique=True)

    op.create_table(
        "usage_ledger",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="generation"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("chapters_generated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_ledger_user_id", "usage_ledger", ["user_id"], unique=False)
    op.create_index("ix_usage_ledger_novel_id", "usage_ledger", ["novel_id"], unique=False)
    op.create_index("ix_usage_ledger_task_id", "usage_ledger", ["task_id"], unique=False)
    op.create_index("idx_usage_ledger_user_created", "usage_ledger", ["user_id", "created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_usage_ledger_user_created", table_name="usage_ledger")
    op.drop_index("ix_usage_ledger_task_id", table_name="usage_ledger")
    op.drop_index("ix_usage_ledger_novel_id", table_name="usage_ledger")
    op.drop_index("ix_usage_ledger_user_id", table_name="usage_ledger")
    op.drop_table("usage_ledger")

    op.drop_index("idx_user_quotas_user_plan", table_name="user_quotas")
    op.drop_table("user_quotas")

    op.drop_index("ix_generation_tasks_trace_id", table_name="generation_tasks")
    op.drop_column("generation_tasks", "trace_id")

