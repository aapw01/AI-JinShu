"""add story_relations table

Revision ID: b71194aec847
Revises: 002
Create Date: 2026-03-27 09:44:25.364210

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b71194aec847'
down_revision: Union[str, None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('story_relations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('novel_id', sa.Integer(), nullable=False),
    sa.Column('novel_version_id', sa.Integer(), nullable=True),
    sa.Column('source', sa.String(length=255), nullable=False),
    sa.Column('target', sa.String(length=255), nullable=False),
    sa.Column('relation_type', sa.String(length=64), nullable=True),
    sa.Column('description', sa.String(length=512), nullable=True),
    sa.Column('sentiment', sa.String(length=32), nullable=True),
    sa.Column('chapter_num', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['novel_id'], ['novels.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['novel_version_id'], ['novel_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_story_relations_novel_version_source_target', 'story_relations', ['novel_id', 'novel_version_id', 'source', 'target'], unique=True)
    op.create_index(op.f('ix_story_relations_novel_id'), 'story_relations', ['novel_id'], unique=False)
    op.create_index(op.f('ix_story_relations_novel_version_id'), 'story_relations', ['novel_version_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_story_relations_novel_version_id'), table_name='story_relations')
    op.drop_index(op.f('ix_story_relations_novel_id'), table_name='story_relations')
    op.drop_index('idx_story_relations_novel_version_source_target', table_name='story_relations')
    op.drop_table('story_relations')
