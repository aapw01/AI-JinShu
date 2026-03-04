"""add admin-managed system settings tables

Revision ID: 018
Revises: 017
Create Date: 2026-03-03 23:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "system_model_providers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider_key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("adapter_type", sa.String(length=64), nullable=False, server_default="openai_compatible"),
        sa.Column("base_url", sa.String(length=512), nullable=True),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=True),
        sa.Column("api_key_is_encrypted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_system_model_providers_provider_key", "system_model_providers", ["provider_key"], unique=True)
    op.create_index("idx_system_model_providers_priority", "system_model_providers", ["priority"], unique=False)

    op.create_table(
        "system_model_definitions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("provider_id", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("model_type", sa.String(length=32), nullable=False, server_default="chat"),
        sa.Column("is_default", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_enabled", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["provider_id"], ["system_model_providers.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_system_model_definitions_provider_id", "system_model_definitions", ["provider_id"], unique=False)
    op.create_index(
        "idx_system_model_definitions_provider_name_type",
        "system_model_definitions",
        ["provider_id", "model_name", "model_type"],
        unique=True,
    )
    op.create_index(
        "idx_system_model_definitions_provider_type",
        "system_model_definitions",
        ["provider_id", "model_type"],
        unique=False,
    )

    op.create_table(
        "system_runtime_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("setting_key", sa.String(length=128), nullable=False),
        sa.Column("setting_value_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_system_runtime_settings_setting_key", "system_runtime_settings", ["setting_key"], unique=True)
    op.create_index("idx_system_runtime_settings_key", "system_runtime_settings", ["setting_key"], unique=True)


def downgrade() -> None:
    op.drop_index("idx_system_runtime_settings_key", table_name="system_runtime_settings")
    op.drop_index("ix_system_runtime_settings_setting_key", table_name="system_runtime_settings")
    op.drop_table("system_runtime_settings")

    op.drop_index("idx_system_model_definitions_provider_type", table_name="system_model_definitions")
    op.drop_index("idx_system_model_definitions_provider_name_type", table_name="system_model_definitions")
    op.drop_index("ix_system_model_definitions_provider_id", table_name="system_model_definitions")
    op.drop_table("system_model_definitions")

    op.drop_index("idx_system_model_providers_priority", table_name="system_model_providers")
    op.drop_index("ix_system_model_providers_provider_key", table_name="system_model_providers")
    op.drop_table("system_model_providers")
