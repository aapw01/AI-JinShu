from __future__ import annotations

from app.services.memory.context_blocks import ContextBlock, select_context_blocks


def test_context_block_selector_keeps_required_and_drops_optional_over_budget():
    selection = select_context_blocks(
        [
            ContextBlock(
                block_id="outline",
                source_type="outline_contract",
                tier="required",
                priority=1,
                approx_tokens=90,
                value={"chapter_objective": "推进主线"},
            ),
            ContextBlock(
                block_id="recent",
                source_type="recent_window",
                tier="preferred",
                priority=2,
                approx_tokens=40,
                value="上一章结尾",
            ),
            ContextBlock(
                block_id="rag",
                source_type="knowledge_chunks",
                tier="optional",
                priority=9,
                approx_tokens=40,
                value=[{"id": "k1", "content": "资料"}],
            ),
        ],
        token_budget=120,
    )

    assert selection.included_block_ids == ["outline"]
    assert selection.dropped_block_ids == ["recent", "rag"]
    assert selection.used_tokens == 90
    assert selection.blocks_by_id["outline"].included is True
    assert selection.blocks_by_id["recent"].included is False


def test_context_block_selector_orders_preferred_before_optional():
    selection = select_context_blocks(
        [
            ContextBlock("optional", "knowledge_chunks", "optional", 1, 40, "optional"),
            ContextBlock("preferred", "recent_window", "preferred", 9, 40, "preferred"),
        ],
        token_budget=50,
    )

    assert selection.included_block_ids == ["preferred"]
    assert selection.dropped_block_ids == ["optional"]


def test_context_block_selector_keeps_character_focus_before_late_optional():
    selection = select_context_blocks(
        [
            ContextBlock("outline", "outline_contract", "required", 1, 70, {"chapter_objective": "推进"}),
            ContextBlock("character_focus", "character_focus_pack", "preferred", 3, 35, {"characters": [{"name": "林秋"}]}),
            ContextBlock("rag", "knowledge_chunks", "optional", 10, 35, [{"content": "资料"}]),
        ],
        token_budget=110,
    )

    assert selection.included_block_ids == ["outline", "character_focus"]
    assert selection.dropped_block_ids == ["rag"]
