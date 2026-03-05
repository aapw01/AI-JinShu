"""storyboard v2 runtime, preflight, and export tables

Revision ID: 019
Revises: 018
Create Date: 2026-03-04 14:40:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("storyboard_projects", sa.Column("source_novel_version_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_storyboard_projects_source_novel_version_id",
        "storyboard_projects",
        "novel_versions",
        ["source_novel_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_storyboard_projects_source_novel_version_id",
        "storyboard_projects",
        ["source_novel_version_id"],
        unique=False,
    )

    op.create_table(
        "storyboard_source_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("storyboard_project_id", sa.Integer(), nullable=False),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("novel_version_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("chapters_json", sa.JSON(), nullable=True),
        sa.Column("character_profiles_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["storyboard_project_id"], ["storyboard_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["novel_version_id"], ["novel_versions.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_storyboard_source_snapshots_project",
        "storyboard_source_snapshots",
        ["storyboard_project_id"],
        unique=False,
    )
    op.create_index(
        "idx_storyboard_source_snapshots_hash",
        "storyboard_source_snapshots",
        ["snapshot_hash"],
        unique=False,
    )

    op.create_table(
        "storyboard_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("storyboard_project_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_uuid", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("run_state", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("current_phase", sa.String(length=64), nullable=True),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_category", sa.String(length=32), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["storyboard_project_id"], ["storyboard_projects.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_storyboard_runs_public_id", "storyboard_runs", ["public_id"], unique=True)
    op.create_index("idx_storyboard_runs_project_created", "storyboard_runs", ["storyboard_project_id", "created_at"], unique=False)
    op.create_index("idx_storyboard_runs_project_status", "storyboard_runs", ["storyboard_project_id", "status"], unique=False)
    op.create_index("idx_storyboard_runs_project_idempotency", "storyboard_runs", ["storyboard_project_id", "idempotency_key"], unique=False)

    op.create_table(
        "storyboard_run_lanes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("storyboard_run_id", sa.Integer(), nullable=False),
        sa.Column("storyboard_project_id", sa.Integer(), nullable=False),
        sa.Column("lane", sa.String(length=32), nullable=False),
        sa.Column("storyboard_version_id", sa.Integer(), nullable=False),
        sa.Column("creation_task_public_id", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("run_state", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("current_phase", sa.String(length=64), nullable=True),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_category", sa.String(length=32), nullable=True),
        sa.Column("gate_report_json", sa.JSON(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["storyboard_run_id"], ["storyboard_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["storyboard_project_id"], ["storyboard_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["storyboard_version_id"], ["storyboard_versions.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_storyboard_run_lanes_run_lane", "storyboard_run_lanes", ["storyboard_run_id", "lane"], unique=True)
    op.create_index("idx_storyboard_run_lanes_project_status", "storyboard_run_lanes", ["storyboard_project_id", "status"], unique=False)
    op.create_index(
        "idx_storyboard_run_lanes_creation_public_id",
        "storyboard_run_lanes",
        ["creation_task_public_id"],
        unique=False,
    )

    op.create_table(
        "storyboard_character_cards",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("storyboard_project_id", sa.Integer(), nullable=False),
        sa.Column("storyboard_version_id", sa.Integer(), nullable=False),
        sa.Column("lane", sa.String(length=32), nullable=False),
        sa.Column("character_key", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("skin_tone", sa.String(length=64), nullable=False),
        sa.Column("ethnicity", sa.String(length=64), nullable=False),
        sa.Column("master_prompt_text", sa.Text(), nullable=False),
        sa.Column("negative_prompt_text", sa.Text(), nullable=True),
        sa.Column("style_tags_json", sa.JSON(), nullable=True),
        sa.Column("consistency_anchors_json", sa.JSON(), nullable=True),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["storyboard_project_id"], ["storyboard_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["storyboard_version_id"], ["storyboard_versions.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "idx_storyboard_character_cards_version_lane_character",
        "storyboard_character_cards",
        ["storyboard_version_id", "lane", "character_key"],
        unique=True,
    )

    op.create_table(
        "storyboard_gate_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("storyboard_project_id", sa.Integer(), nullable=False),
        sa.Column("storyboard_run_id", sa.Integer(), nullable=True),
        sa.Column("storyboard_version_id", sa.Integer(), nullable=True),
        sa.Column("gate_type", sa.String(length=32), nullable=False),
        sa.Column("gate_status", sa.String(length=32), nullable=False),
        sa.Column("missing_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("report_json", sa.JSON(), nullable=True),
        sa.Column("created_by_user_uuid", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["storyboard_project_id"], ["storyboard_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["storyboard_run_id"], ["storyboard_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["storyboard_version_id"], ["storyboard_versions.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "idx_storyboard_gate_reports_project_type",
        "storyboard_gate_reports",
        ["storyboard_project_id", "gate_type"],
        unique=False,
    )

    op.create_table(
        "storyboard_exports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("storyboard_project_id", sa.Integer(), nullable=False),
        sa.Column("storyboard_version_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_uuid", sa.String(length=36), nullable=False),
        sa.Column("format", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="queued"),
        sa.Column("content_type", sa.String(length=100), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("storage_path", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["storyboard_project_id"], ["storyboard_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["storyboard_version_id"], ["storyboard_versions.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_storyboard_exports_public_id", "storyboard_exports", ["public_id"], unique=True)
    op.create_index("idx_storyboard_exports_project_status", "storyboard_exports", ["storyboard_project_id", "status"], unique=False)
    op.create_index(
        "idx_storyboard_exports_project_version_idempotency",
        "storyboard_exports",
        ["storyboard_project_id", "storyboard_version_id", "idempotency_key"],
        unique=False,
    )

    op.create_table(
        "storyboard_events_outbox",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("storyboard_project_id", sa.Integer(), nullable=False),
        sa.Column("storyboard_run_id", sa.Integer(), nullable=True),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["storyboard_project_id"], ["storyboard_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["storyboard_run_id"], ["storyboard_runs.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_storyboard_outbox_topic_status", "storyboard_events_outbox", ["topic", "status"], unique=False)
    op.create_index("idx_storyboard_outbox_event_key", "storyboard_events_outbox", ["event_key"], unique=False)

    op.create_table(
        "storyboard_audit_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("storyboard_project_id", sa.Integer(), nullable=False),
        sa.Column("storyboard_run_id", sa.Integer(), nullable=True),
        sa.Column("storyboard_version_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor_user_uuid", sa.String(length=36), nullable=False),
        sa.Column("detail_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["storyboard_project_id"], ["storyboard_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["storyboard_run_id"], ["storyboard_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["storyboard_version_id"], ["storyboard_versions.id"], ondelete="SET NULL"),
    )
    op.create_index("idx_storyboard_audit_project_action", "storyboard_audit_logs", ["storyboard_project_id", "action"], unique=False)
    op.create_index("idx_storyboard_audit_actor", "storyboard_audit_logs", ["actor_user_uuid"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_storyboard_audit_actor", table_name="storyboard_audit_logs")
    op.drop_index("idx_storyboard_audit_project_action", table_name="storyboard_audit_logs")
    op.drop_table("storyboard_audit_logs")

    op.drop_index("idx_storyboard_outbox_event_key", table_name="storyboard_events_outbox")
    op.drop_index("idx_storyboard_outbox_topic_status", table_name="storyboard_events_outbox")
    op.drop_table("storyboard_events_outbox")

    op.drop_index("idx_storyboard_exports_project_version_idempotency", table_name="storyboard_exports")
    op.drop_index("idx_storyboard_exports_project_status", table_name="storyboard_exports")
    op.drop_index("ix_storyboard_exports_public_id", table_name="storyboard_exports")
    op.drop_table("storyboard_exports")

    op.drop_index("idx_storyboard_gate_reports_project_type", table_name="storyboard_gate_reports")
    op.drop_table("storyboard_gate_reports")

    op.drop_index("idx_storyboard_character_cards_version_lane_character", table_name="storyboard_character_cards")
    op.drop_table("storyboard_character_cards")

    op.drop_index("idx_storyboard_run_lanes_creation_public_id", table_name="storyboard_run_lanes")
    op.drop_index("idx_storyboard_run_lanes_project_status", table_name="storyboard_run_lanes")
    op.drop_index("idx_storyboard_run_lanes_run_lane", table_name="storyboard_run_lanes")
    op.drop_table("storyboard_run_lanes")

    op.drop_index("idx_storyboard_runs_project_idempotency", table_name="storyboard_runs")
    op.drop_index("idx_storyboard_runs_project_status", table_name="storyboard_runs")
    op.drop_index("idx_storyboard_runs_project_created", table_name="storyboard_runs")
    op.drop_index("ix_storyboard_runs_public_id", table_name="storyboard_runs")
    op.drop_table("storyboard_runs")

    op.drop_index("idx_storyboard_source_snapshots_hash", table_name="storyboard_source_snapshots")
    op.drop_index("idx_storyboard_source_snapshots_project", table_name="storyboard_source_snapshots")
    op.drop_table("storyboard_source_snapshots")

    op.drop_index("idx_storyboard_projects_source_novel_version_id", table_name="storyboard_projects")
    op.drop_constraint("fk_storyboard_projects_source_novel_version_id", "storyboard_projects", type_="foreignkey")
    op.drop_column("storyboard_projects", "source_novel_version_id")
