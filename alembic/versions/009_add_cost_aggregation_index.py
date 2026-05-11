"""add partial index on agent_events for cost_usd aggregation

Revision ID: 009
Revises: 008
Create Date: 2026-05-11 14:00:00.000000

scheduler 每个 tick 都会调 ``check_budget`` 聚合 ``agent_events.payload.cost_usd``，
长篇小说下 events 量很容易过百万。本迁移加一条 PG-only 的 partial index：

    CREATE INDEX idx_agent_events_cost ON agent_events (novel_id)
    WHERE payload ? 'cost_usd';

SQLite 无 partial JSON 索引能力 → 跳过；测试环境的事件量也无需索引。
"""

from __future__ import annotations

from alembic import op


revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def _is_postgres() -> bool:
    bind = op.get_bind()
    try:
        return (bind.dialect.name or "").lower() == "postgresql"
    except Exception:
        return False


def upgrade() -> None:
    if not _is_postgres():
        return
    # Partial index: 只对带 cost_usd 的事件建索引，scheduler 的预算聚合
    # 直接走这条 index，避免全表扫。
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_events_cost_novel "
        "ON agent_events (novel_id) "
        "WHERE payload ? 'cost_usd'"
    )


def downgrade() -> None:
    if not _is_postgres():
        return
    op.execute("DROP INDEX IF EXISTS idx_agent_events_cost_novel")
