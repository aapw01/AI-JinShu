"""Token-budget selection helpers for generation context blocks."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

ContextTier = Literal["required", "preferred", "optional"]
_TIER_ORDER: dict[str, int] = {"required": 0, "preferred": 1, "optional": 2}


@dataclass(frozen=True)
class ContextBlock:
    """A named context unit that can be selected or dropped under token budget."""

    block_id: str
    source_type: str
    tier: ContextTier
    priority: int
    approx_tokens: int
    value: Any
    field_names: tuple[str, ...] = ()
    included: bool = False
    drop_reason: str = ""


@dataclass(frozen=True)
class ContextBlockSelection:
    """Result of applying a token budget to context blocks."""

    blocks: tuple[ContextBlock, ...]
    included_block_ids: list[str]
    dropped_block_ids: list[str]
    used_tokens: int
    token_budget: int

    @property
    def blocks_by_id(self) -> dict[str, ContextBlock]:
        """Return selected blocks keyed by block ID."""
        return {block.block_id: block for block in self.blocks}

    def as_metadata(self) -> dict[str, Any]:
        """Return JSON-safe selection metadata for snapshots and debugging."""
        return {
            "included_block_ids": list(self.included_block_ids),
            "dropped_block_ids": list(self.dropped_block_ids),
            "used_tokens": int(self.used_tokens),
            "token_budget": int(self.token_budget),
            "blocks": [
                {
                    "block_id": block.block_id,
                    "source_type": block.source_type,
                    "tier": block.tier,
                    "priority": int(block.priority),
                    "approx_tokens": max(0, int(block.approx_tokens)),
                    "included": bool(block.included),
                    "drop_reason": block.drop_reason,
                }
                for block in self.blocks
            ],
        }


def _is_empty(value: Any) -> bool:
    return value in (None, "", [], {})


def select_context_blocks(blocks: list[ContextBlock], *, token_budget: int) -> ContextBlockSelection:
    """Select blocks by tier and priority, keeping all required blocks."""
    budget = max(0, int(token_budget or 0))
    used = 0
    selected: list[ContextBlock] = []

    ordered = sorted(
        blocks,
        key=lambda block: (
            _TIER_ORDER.get(block.tier, 99),
            int(block.priority),
            block.block_id,
        ),
    )
    for block in ordered:
        approx = max(0, int(block.approx_tokens or 0))
        if _is_empty(block.value):
            selected.append(replace(block, included=False, drop_reason="empty"))
            continue
        if block.tier == "required":
            selected.append(replace(block, included=True, drop_reason=""))
            used += approx
            continue
        if used + approx <= budget:
            selected.append(replace(block, included=True, drop_reason=""))
            used += approx
        else:
            selected.append(replace(block, included=False, drop_reason="token_budget_exceeded"))

    included = [block.block_id for block in selected if block.included]
    dropped = [block.block_id for block in selected if not block.included]
    return ContextBlockSelection(
        blocks=tuple(selected),
        included_block_ids=included,
        dropped_block_ids=dropped,
        used_tokens=used,
        token_budget=budget,
    )
