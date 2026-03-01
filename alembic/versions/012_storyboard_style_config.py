"""add storyboard style config json

Revision ID: 012
Revises: 011
Create Date: 2026-02-27 20:10:00.000000
"""
from typing import Union

from alembic import op
import sqlalchemy as sa


revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("storyboard_projects", sa.Column("config_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("storyboard_projects", "config_json")
