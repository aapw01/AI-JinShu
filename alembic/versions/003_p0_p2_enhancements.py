"""P0-P2 enhancements: status machine, metrics, reports.

Revision ID: 003
Revises: 002
Create Date: 2026-02-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("chapters", sa.Column("status", sa.String(50), nullable=True))
    op.execute("UPDATE chapters SET status='completed' WHERE status IS NULL")
    op.alter_column("chapters", "status", server_default="pending")
    op.add_column("chapters", sa.Column("language_quality_report", sa.Text(), nullable=True))

    op.add_column("generation_tasks", sa.Column("current_phase", sa.String(50), nullable=True))
    op.add_column("generation_tasks", sa.Column("total_chapters", sa.Integer(), nullable=True))
    op.execute("UPDATE generation_tasks SET total_chapters = num_chapters WHERE total_chapters IS NULL")
    op.alter_column("generation_tasks", "total_chapters", server_default="0")
    op.add_column("generation_tasks", sa.Column("token_usage_input", sa.Integer(), nullable=True))
    op.add_column("generation_tasks", sa.Column("token_usage_output", sa.Integer(), nullable=True))
    op.add_column("generation_tasks", sa.Column("estimated_cost", sa.Float(), nullable=True))
    op.add_column("generation_tasks", sa.Column("outline_confirmed", sa.Integer(), nullable=True))
    op.execute("UPDATE generation_tasks SET outline_confirmed = 1 WHERE outline_confirmed IS NULL")
    op.alter_column("generation_tasks", "outline_confirmed", server_default="1")
    op.add_column("generation_tasks", sa.Column("final_report", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("generation_tasks", "final_report")
    op.drop_column("generation_tasks", "outline_confirmed")
    op.drop_column("generation_tasks", "estimated_cost")
    op.drop_column("generation_tasks", "token_usage_output")
    op.drop_column("generation_tasks", "token_usage_input")
    op.drop_column("generation_tasks", "total_chapters")
    op.drop_column("generation_tasks", "current_phase")

    op.drop_column("chapters", "language_quality_report")
    op.drop_column("chapters", "status")
