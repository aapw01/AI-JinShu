"""create unified creation_tasks table and backfill generation/rewrite tasks

Revision ID: 014
Revises: 013
Create Date: 2026-02-28 12:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "creation_tasks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("user_uuid", sa.String(length=36), nullable=False),
        sa.Column("task_type", sa.String(length=32), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("queue_seq", sa.BigInteger(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_retries", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("worker_task_id", sa.String(length=255), nullable=True),
        sa.Column("phase", sa.String(length=64), nullable=True),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_category", sa.String(length=32), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_creation_tasks_public_id", "creation_tasks", ["public_id"], unique=True)
    op.create_index("ix_creation_tasks_user_uuid", "creation_tasks", ["user_uuid"], unique=False)
    op.create_index("ix_creation_tasks_queue_seq", "creation_tasks", ["queue_seq"], unique=False)
    op.create_index("ix_creation_tasks_worker_task_id", "creation_tasks", ["worker_task_id"], unique=False)
    op.create_index(
        "idx_creation_tasks_user_status_queue",
        "creation_tasks",
        ["user_uuid", "status", "queue_seq"],
        unique=False,
    )
    op.create_index(
        "idx_creation_tasks_type_resource",
        "creation_tasks",
        ["task_type", "resource_type", "resource_id"],
        unique=False,
    )

    # Backfill generation tasks.
    op.execute(
        sa.text(
            """
            INSERT INTO creation_tasks (
                public_id, user_uuid, task_type, resource_type, resource_id, status,
                priority, queue_seq, retry_count, max_retries, worker_task_id, phase,
                progress, message, error_code, error_category, error_detail,
                payload_json, result_json, created_at, updated_at, started_at, finished_at
            )
            SELECT
                COALESCE(NULLIF(g.task_id, ''), ('gen-' || g.id)),
                n.user_id,
                'generation',
                'novel',
                g.novel_id,
                CASE
                    WHEN g.status IN ('submitted', 'retrying', 'awaiting_outline_confirmation') THEN 'queued'
                    WHEN g.status = 'running' THEN 'running'
                    WHEN g.status = 'paused' THEN 'paused'
                    WHEN g.status = 'completed' THEN 'completed'
                    WHEN g.status = 'cancelled' THEN 'cancelled'
                    ELSE 'failed'
                END,
                100,
                NULL,
                0,
                3,
                g.task_id,
                g.current_phase,
                COALESCE(g.progress, 0),
                g.message,
                g.error_code,
                g.error_category,
                g.error,
                NULL,
                NULL,
                g.created_at,
                g.updated_at,
                CASE WHEN g.status = 'running' THEN g.created_at ELSE NULL END,
                CASE WHEN g.status IN ('completed', 'failed', 'cancelled') THEN g.updated_at ELSE NULL END
            FROM generation_tasks g
            JOIN novels n ON n.id = g.novel_id
            WHERE n.user_id IS NOT NULL
            """
        )
    )

    # Backfill rewrite requests.
    op.execute(
        sa.text(
            """
            INSERT INTO creation_tasks (
                public_id, user_uuid, task_type, resource_type, resource_id, status,
                priority, queue_seq, retry_count, max_retries, worker_task_id, phase,
                progress, message, error_code, error_category, error_detail,
                payload_json, result_json, created_at, updated_at, started_at, finished_at
            )
            SELECT
                COALESCE(NULLIF(r.task_id, ''), ('rewrite-' || r.id)),
                n.user_id,
                'rewrite',
                'rewrite_request',
                r.id,
                CASE
                    WHEN r.status = 'submitted' THEN 'queued'
                    WHEN r.status = 'running' THEN 'running'
                    WHEN r.status = 'paused' THEN 'paused'
                    WHEN r.status = 'completed' THEN 'completed'
                    WHEN r.status = 'cancelled' THEN 'cancelled'
                    ELSE 'failed'
                END,
                100,
                NULL,
                0,
                3,
                r.task_id,
                NULL,
                COALESCE(r.progress, 0),
                r.message,
                NULL,
                NULL,
                r.error,
                NULL,
                NULL,
                r.created_at,
                r.updated_at,
                CASE WHEN r.status = 'running' THEN r.created_at ELSE NULL END,
                CASE WHEN r.status IN ('completed', 'failed', 'cancelled') THEN r.updated_at ELSE NULL END
            FROM rewrite_requests r
            JOIN novels n ON n.id = r.novel_id
            WHERE n.user_id IS NOT NULL
            """
        )
    )

    op.execute(sa.text("UPDATE creation_tasks SET queue_seq = id WHERE queue_seq IS NULL"))


def downgrade() -> None:
    op.drop_index("idx_creation_tasks_type_resource", table_name="creation_tasks")
    op.drop_index("idx_creation_tasks_user_status_queue", table_name="creation_tasks")
    op.drop_index("ix_creation_tasks_worker_task_id", table_name="creation_tasks")
    op.drop_index("ix_creation_tasks_queue_seq", table_name="creation_tasks")
    op.drop_index("ix_creation_tasks_user_uuid", table_name="creation_tasks")
    op.drop_index("ix_creation_tasks_public_id", table_name="creation_tasks")
    op.drop_table("creation_tasks")
