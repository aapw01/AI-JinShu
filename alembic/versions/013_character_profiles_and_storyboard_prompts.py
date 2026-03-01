"""add character profile and storyboard character prompt tables

Revision ID: 013
Revises: 012
Create Date: 2026-02-27 16:40:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "story_character_profiles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("novel_id", sa.Integer(), sa.ForeignKey("novels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("character_key", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("gender_presentation", sa.String(length=64), nullable=True),
        sa.Column("age_band", sa.String(length=64), nullable=True),
        sa.Column("skin_tone", sa.String(length=64), nullable=True),
        sa.Column("ethnicity", sa.String(length=64), nullable=True),
        sa.Column("body_type", sa.String(length=128), nullable=True),
        sa.Column("face_features", sa.String(length=255), nullable=True),
        sa.Column("hair_style", sa.String(length=128), nullable=True),
        sa.Column("hair_color", sa.String(length=64), nullable=True),
        sa.Column("eye_color", sa.String(length=64), nullable=True),
        sa.Column("wardrobe_base_style", sa.String(length=255), nullable=True),
        sa.Column("signature_items_json", sa.JSON(), nullable=True),
        sa.Column("visual_do_not_change_json", sa.JSON(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("updated_chapter_num", sa.Integer(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "idx_story_character_profiles_novel_character",
        "story_character_profiles",
        ["novel_id", "character_key"],
        unique=True,
    )

    op.create_table(
        "storyboard_character_prompts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "storyboard_project_id",
            sa.Integer(),
            sa.ForeignKey("storyboard_projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "storyboard_version_id",
            sa.Integer(),
            sa.ForeignKey("storyboard_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
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
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "idx_storyboard_character_prompts_version_lane_character",
        "storyboard_character_prompts",
        ["storyboard_version_id", "lane", "character_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("idx_storyboard_character_prompts_version_lane_character", table_name="storyboard_character_prompts")
    op.drop_table("storyboard_character_prompts")
    op.drop_index("idx_story_character_profiles_novel_character", table_name="story_character_profiles")
    op.drop_table("story_character_profiles")
