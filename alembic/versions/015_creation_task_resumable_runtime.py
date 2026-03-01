"""add resumable runtime fields and creation task checkpoints

Revision ID: 015
Revises: 014
Create Date: 2026-02-28 16:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("creation_tasks", sa.Column("resume_cursor_json", sa.JSON(), nullable=True))
    op.add_column("creation_tasks", sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True))
    op.add_column("creation_tasks", sa.Column("worker_lease_expires_at", sa.DateTime(), nullable=True))
    op.add_column("creation_tasks", sa.Column("recovery_count", sa.Integer(), nullable=False, server_default="0"))

    op.execute(sa.text("UPDATE creation_tasks SET resume_cursor_json = '{}'::json WHERE resume_cursor_json IS NULL"))
    op.alter_column("creation_tasks", "resume_cursor_json", nullable=False)
    op.alter_column("creation_tasks", "recovery_count", server_default=None)

    op.create_table(
        "creation_task_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("creation_task_id", sa.Integer(), sa.ForeignKey("creation_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("unit_type", sa.String(length=32), nullable=False, server_default="chapter"),
        sa.Column("unit_no", sa.Integer(), nullable=False),
        sa.Column("partition", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="completed"),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "idx_creation_task_checkpoints_task_unit_partition",
        "creation_task_checkpoints",
        ["creation_task_id", "unit_type", "partition", "unit_no"],
        unique=False,
    )
    op.create_index(
        "uq_creation_task_checkpoints_task_unit_partition",
        "creation_task_checkpoints",
        ["creation_task_id", "unit_type", "unit_no", "partition"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_creation_task_checkpoints_task_unit_partition", table_name="creation_task_checkpoints")
    op.drop_index("idx_creation_task_checkpoints_task_unit_partition", table_name="creation_task_checkpoints")
    op.drop_table("creation_task_checkpoints")

    op.drop_column("creation_tasks", "recovery_count")
    op.drop_column("creation_tasks", "worker_lease_expires_at")
    op.drop_column("creation_tasks", "last_heartbeat_at")
    op.drop_column("creation_tasks", "resume_cursor_json")
