"""add storyboard director professional tables

Revision ID: 011
Revises: 010
Create Date: 2026-02-27 18:30:00.000000
"""
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "storyboard_projects",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("uuid", sa.String(length=36), nullable=True),
        sa.Column("novel_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_uuid", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("target_episodes", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("target_episode_seconds", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("style_profile", sa.String(length=100), nullable=True),
        sa.Column("professional_mode", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("audience_goal", sa.String(length=100), nullable=True),
        sa.Column("output_lanes", sa.JSON(), nullable=True),
        sa.Column("active_lane", sa.String(length=32), nullable=False, server_default="vertical_feed"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["novel_id"], ["novels.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("uuid"),
    )
    op.create_index("ix_storyboard_projects_uuid", "storyboard_projects", ["uuid"], unique=False)
    op.create_index("ix_storyboard_projects_novel_id", "storyboard_projects", ["novel_id"], unique=False)
    op.create_index("ix_storyboard_projects_owner_user_uuid", "storyboard_projects", ["owner_user_uuid"], unique=False)

    op.create_table(
        "storyboard_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("storyboard_project_id", sa.Integer(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", sa.Integer(), nullable=True),
        sa.Column("lane", sa.String(length=32), nullable=False, server_default="vertical_feed"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("is_default", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_final", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("quality_report_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["parent_version_id"], ["storyboard_versions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["storyboard_project_id"], ["storyboard_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_storyboard_versions_storyboard_project_id", "storyboard_versions", ["storyboard_project_id"], unique=False)
    op.create_index(
        "idx_storyboard_versions_project_lane_version",
        "storyboard_versions",
        ["storyboard_project_id", "lane", "version_no"],
        unique=True,
    )
    op.create_index(
        "idx_storyboard_versions_project_default",
        "storyboard_versions",
        ["storyboard_project_id", "is_default"],
        unique=False,
    )

    op.create_table(
        "storyboard_shots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("storyboard_version_id", sa.Integer(), nullable=False),
        sa.Column("episode_no", sa.Integer(), nullable=False),
        sa.Column("scene_no", sa.Integer(), nullable=False),
        sa.Column("shot_no", sa.Integer(), nullable=False),
        sa.Column("location", sa.String(length=255), nullable=True),
        sa.Column("time_of_day", sa.String(length=32), nullable=True),
        sa.Column("shot_size", sa.String(length=50), nullable=True),
        sa.Column("camera_angle", sa.String(length=50), nullable=True),
        sa.Column("camera_move", sa.String(length=50), nullable=True),
        sa.Column("duration_sec", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("characters_json", sa.JSON(), nullable=True),
        sa.Column("action", sa.Text(), nullable=True),
        sa.Column("dialogue", sa.Text(), nullable=True),
        sa.Column("emotion_beat", sa.String(length=255), nullable=True),
        sa.Column("transition", sa.String(length=50), nullable=True),
        sa.Column("sound_hint", sa.String(length=255), nullable=True),
        sa.Column("production_note", sa.Text(), nullable=True),
        sa.Column("blocking", sa.Text(), nullable=True),
        sa.Column("motivation", sa.String(length=255), nullable=True),
        sa.Column("performance_note", sa.Text(), nullable=True),
        sa.Column("continuity_anchor", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["storyboard_version_id"], ["storyboard_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_storyboard_shots_storyboard_version_id", "storyboard_shots", ["storyboard_version_id"], unique=False)
    op.create_index(
        "idx_storyboard_shots_version_episode_scene_shot",
        "storyboard_shots",
        ["storyboard_version_id", "episode_no", "scene_no", "shot_no"],
        unique=True,
    )

    op.create_table(
        "storyboard_tasks",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("storyboard_project_id", sa.Integer(), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="submitted"),
        sa.Column("run_state", sa.String(length=32), nullable=False, server_default="submitted"),
        sa.Column("current_phase", sa.String(length=50), nullable=True),
        sa.Column("current_lane", sa.String(length=32), nullable=True),
        sa.Column("progress", sa.Float(), nullable=False, server_default="0"),
        sa.Column("current_episode", sa.Integer(), nullable=True),
        sa.Column("eta_seconds", sa.Integer(), nullable=True),
        sa.Column("message", sa.String(length=500), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_category", sa.String(length=32), nullable=True),
        sa.Column("retryable", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("gate_report_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["storyboard_project_id"], ["storyboard_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index("ix_storyboard_tasks_task_id", "storyboard_tasks", ["task_id"], unique=False)
    op.create_index("ix_storyboard_tasks_storyboard_project_id", "storyboard_tasks", ["storyboard_project_id"], unique=False)
    op.create_index("ix_storyboard_tasks_trace_id", "storyboard_tasks", ["trace_id"], unique=False)
    op.create_index(
        "idx_storyboard_tasks_project_status",
        "storyboard_tasks",
        ["storyboard_project_id", "status"],
        unique=False,
    )

    op.create_table(
        "storyboard_assertions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("storyboard_project_id", sa.Integer(), nullable=False),
        sa.Column("user_uuid", sa.String(length=36), nullable=False),
        sa.Column("assertion_type", sa.String(length=50), nullable=False),
        sa.Column("assertion_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["storyboard_project_id"], ["storyboard_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_storyboard_assertions_storyboard_project_id", "storyboard_assertions", ["storyboard_project_id"], unique=False)
    op.create_index("ix_storyboard_assertions_user_uuid", "storyboard_assertions", ["user_uuid"], unique=False)
    op.create_index(
        "idx_storyboard_assertions_project_type",
        "storyboard_assertions",
        ["storyboard_project_id", "assertion_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_storyboard_assertions_project_type", table_name="storyboard_assertions")
    op.drop_index("ix_storyboard_assertions_user_uuid", table_name="storyboard_assertions")
    op.drop_index("ix_storyboard_assertions_storyboard_project_id", table_name="storyboard_assertions")
    op.drop_table("storyboard_assertions")

    op.drop_index("idx_storyboard_tasks_project_status", table_name="storyboard_tasks")
    op.drop_index("ix_storyboard_tasks_trace_id", table_name="storyboard_tasks")
    op.drop_index("ix_storyboard_tasks_storyboard_project_id", table_name="storyboard_tasks")
    op.drop_index("ix_storyboard_tasks_task_id", table_name="storyboard_tasks")
    op.drop_table("storyboard_tasks")

    op.drop_index("idx_storyboard_shots_version_episode_scene_shot", table_name="storyboard_shots")
    op.drop_index("ix_storyboard_shots_storyboard_version_id", table_name="storyboard_shots")
    op.drop_table("storyboard_shots")

    op.drop_index("idx_storyboard_versions_project_default", table_name="storyboard_versions")
    op.drop_index("idx_storyboard_versions_project_lane_version", table_name="storyboard_versions")
    op.drop_index("ix_storyboard_versions_storyboard_project_id", table_name="storyboard_versions")
    op.drop_table("storyboard_versions")

    op.drop_index("ix_storyboard_projects_owner_user_uuid", table_name="storyboard_projects")
    op.drop_index("ix_storyboard_projects_novel_id", table_name="storyboard_projects")
    op.drop_index("ix_storyboard_projects_uuid", table_name="storyboard_projects")
    op.drop_table("storyboard_projects")
