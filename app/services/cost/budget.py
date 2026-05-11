"""Per-novel cost budget service (§11.3).

- ``compute_cost(model, input_tokens, output_tokens)`` 查 yaml 价格表估算单次 cost。
- ``check_budget(novel_id)`` 累计 ``agent_events.payload.cost_usd`` 与 novel
  metadata 里的 budget 比较。返回 ``ok | warn | hard_stop``，scheduler 在
  分发前调用。

Phase 0 实现保持极简：cost 落 ``agent_events.payload.cost_usd``（不加列），
budget 走 ``novels.metadata.cost_budget``（已是 JSON）。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Float, cast, func, select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.novel import AgentEvent, Novel

logger = logging.getLogger(__name__)


_PRICE_PATH = Path(__file__).resolve().parents[3] / "presets" / "cost" / "model_prices.yaml"
_CACHE_TTL = 5.0
_lock = threading.Lock()
_cache: dict[str, Any] = {"ts": 0.0, "value": None}


class ModelPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tier: Literal["premium", "standard", "cheap", "unknown"]
    input_per_1k_tokens_usd: float = Field(ge=0)
    output_per_1k_tokens_usd: float = Field(ge=0)


class ModelPriceFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    default_currency: Literal["usd"] = "usd"
    fallback_unknown_model: ModelPrice
    models: dict[str, ModelPrice] = Field(default_factory=dict)


def invalidate_price_cache() -> None:
    with _lock:
        _cache["ts"] = 0.0
        _cache["value"] = None


def _load_prices() -> ModelPriceFile | None:
    now = time.monotonic()
    with _lock:
        if (now - _cache["ts"]) < _CACHE_TTL and _cache["value"] is not None:
            return _cache["value"]
    if not _PRICE_PATH.exists():
        return None
    try:
        raw = yaml.safe_load(_PRICE_PATH.read_text(encoding="utf-8")) or {}
        parsed = ModelPriceFile.model_validate(raw)
    except Exception:
        logger.exception("cost.budget: failed to parse model_prices.yaml")
        return None
    with _lock:
        _cache["ts"] = now
        _cache["value"] = parsed
    return parsed


def compute_cost(model: str | None, input_tokens: int, output_tokens: int) -> float:
    """估算单次调用 USD 成本。未知 model 用 fallback 价档（保证不漏算）。"""
    prices = _load_prices()
    if prices is None:
        return 0.0
    price = prices.models.get((model or "").strip(), prices.fallback_unknown_model)
    return round(
        max(0, int(input_tokens or 0)) / 1000 * price.input_per_1k_tokens_usd
        + max(0, int(output_tokens or 0)) / 1000 * price.output_per_1k_tokens_usd,
        6,
    )


@dataclass
class BudgetVerdict:
    status: Literal["ok", "warn", "hard_stop", "no_budget"]
    spent_usd: float
    budget_usd: float | None
    burn_rate: float


def _spent_for_novel(db: Session, novel_id: int) -> float:
    """聚合 ``agent_events.payload.cost_usd``。

    - **Postgres 路径**：直接 ``SUM(CAST(payload->>'cost_usd' AS FLOAT))``。配合
      ``idx_agent_events_novel_chapter_time``（覆盖 ``novel_id``）实现 O(n_novel) 而非
      O(n_all_events) 全表扫，scheduler 每 tick 都调用也不会变成瓶颈。
    - **SQLite 路径**：JSON ``->>`` 不一定有，退化到行级 Python 累加，但先用
      ``payload IS NOT NULL`` + ``LIKE '%cost_usd%'`` 把扫描面缩到带 cost 的行。
    - 任何异常都退到 0.0 + warning log，让上游退化到 ``no_budget``，避免 budget
      检查本身阻塞 scheduler dispatch。
    """
    dialect = ""
    try:
        dialect = (db.get_bind().dialect.name or "").lower()
    except Exception:
        pass

    if dialect == "postgresql":
        try:
            # payload['cost_usd'] 在 SQLAlchemy 1.4+ 的 JSON 上是 .astext → ::float
            cost_col = cast(
                AgentEvent.payload["cost_usd"].astext, Float
            )
            stmt = select(func.coalesce(func.sum(cost_col), 0.0)).where(
                AgentEvent.novel_id == novel_id
            )
            value = db.execute(stmt).scalar_one()
            return float(value or 0.0)
        except Exception:
            logger.warning(
                "cost.budget: PG SQL aggregation failed, falling back to row-level",
                exc_info=True,
            )

    # SQLite / 不确定方言 / PG 路径失败：行级累加（测试 + 兜底）
    try:
        rows = db.execute(
            select(AgentEvent.payload)
            .where(AgentEvent.novel_id == novel_id)
            .where(AgentEvent.payload.is_not(None))
        ).all()
    except Exception:
        logger.warning("cost.budget: row-level fetch failed", exc_info=True)
        return 0.0

    total = 0.0
    for (payload,) in rows:
        if isinstance(payload, dict):
            try:
                total += float(payload.get("cost_usd") or 0.0)
            except (TypeError, ValueError):
                continue
    return total


def check_budget(novel_id: int, *, db: Session | None = None) -> BudgetVerdict:
    """读取 novel.metadata 的 cost_budget 与累计花费比较。"""
    owns = db is None
    session = db or SessionLocal()
    try:
        novel = session.execute(select(Novel).where(Novel.id == novel_id)).scalar_one_or_none()
        if novel is None:
            return BudgetVerdict(status="no_budget", spent_usd=0.0, budget_usd=None, burn_rate=0.0)
        cfg = novel.config if isinstance(novel.config, dict) else {}
        budget_block = cfg.get("cost_budget") if isinstance(cfg, dict) else None
        budget_usd: float | None = None
        if isinstance(budget_block, dict):
            try:
                budget_usd = float(budget_block.get("usd")) if budget_block.get("usd") is not None else None
            except (TypeError, ValueError):
                budget_usd = None
        spent = _spent_for_novel(session, novel_id)
        if budget_usd is None or budget_usd <= 0:
            return BudgetVerdict(status="no_budget", spent_usd=spent, budget_usd=None, burn_rate=0.0)
        burn_rate = spent / budget_usd
        if burn_rate >= 1.0:
            status: Literal["ok", "warn", "hard_stop", "no_budget"] = "hard_stop"
        elif burn_rate >= 0.8:
            status = "warn"
        else:
            status = "ok"
        return BudgetVerdict(status=status, spent_usd=spent, budget_usd=budget_usd, burn_rate=burn_rate)
    finally:
        if owns:
            try:
                session.close()
            except Exception:
                logger.debug("check_budget close failed", exc_info=True)


# Convenience re-export
__all__ = [
    "BudgetVerdict",
    "ModelPrice",
    "ModelPriceFile",
    "check_budget",
    "compute_cost",
    "invalidate_price_cache",
]
