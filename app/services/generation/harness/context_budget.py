"""Scenario harness for context block token-budget selection."""
from __future__ import annotations

from typing import Any

from app.services.memory.context_blocks import ContextBlock, select_context_blocks


def _to_context_block(raw: dict[str, Any]) -> ContextBlock:
    return ContextBlock(
        block_id=str(raw.get("block_id") or ""),
        source_type=str(raw.get("source_type") or ""),
        tier=raw.get("tier") or "optional",
        priority=int(raw.get("priority") or 0),
        approx_tokens=int(raw.get("approx_tokens") or 0),
        value=raw.get("value"),
        field_names=tuple(raw.get("field_names") or ()),
    )


def _evaluate_scenario(scenario: dict[str, Any]) -> dict[str, Any]:
    blocks = [_to_context_block(block) for block in scenario.get("blocks") or [] if isinstance(block, dict)]
    selection = select_context_blocks(blocks, token_budget=int(scenario.get("token_budget") or 0))
    included = list(selection.included_block_ids)
    dropped = list(selection.dropped_block_ids)
    must_include = {str(block_id) for block_id in scenario.get("must_include") or []}
    must_drop = {str(block_id) for block_id in scenario.get("must_drop") or []}

    missing_required = sorted(must_include.difference(included))
    not_dropped = sorted(must_drop.difference(dropped))
    reasons = []
    if missing_required:
        reasons.append(f"missing required blocks: {', '.join(missing_required)}")
    if not_dropped:
        reasons.append(f"blocks were not dropped: {', '.join(not_dropped)}")

    return {
        "id": str(scenario.get("id") or ""),
        "passed": not reasons,
        "reasons": reasons,
        "included_block_ids": included,
        "dropped_block_ids": dropped,
        "used_tokens": selection.used_tokens,
        "token_budget": selection.token_budget,
    }


def run_context_budget_harness(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    """Run fixed context-budget scenarios and summarize pass/fail results."""
    scenario_results = [_evaluate_scenario(scenario) for scenario in scenarios]
    total = len(scenario_results)
    passed = sum(1 for result in scenario_results if result["passed"])
    failed = total - passed
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": (passed / total) if total else 1.0,
        "scenario_results": scenario_results,
    }
