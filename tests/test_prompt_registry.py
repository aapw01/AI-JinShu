from __future__ import annotations

from app.prompts.registry import get_prompt_asset, list_prompt_assets, render_prompt_asset


def test_prompt_registry_exposes_core_generation_assets():
    assets = {asset.id: asset for asset in list_prompt_assets()}

    assert "generation.chapter.writer" in assets
    assert "generation.chapter.finalizer" in assets
    assert "generation.review.combined" in assets
    assert "generation.outline.volume_batch" in assets
    assert assets["generation.chapter.writer"].version == "v2"
    assert assets["generation.chapter.writer"].template_name == "next_chapter"
    assert assets["generation.chapter.writer"].output_contract == "ChapterBodySchema"
    assert "character_focus_pack" in assets["generation.chapter.writer"].context_policy.preferred


def test_render_prompt_asset_uses_template_and_returns_meta():
    calls: list[tuple[str, dict]] = []

    def _renderer(template_name: str, **kwargs):
        calls.append((template_name, kwargs))
        return f"rendered:{template_name}:{kwargs['chapter_num']}"

    rendered = render_prompt_asset(
        "generation.chapter.writer",
        renderer=_renderer,
        chapter_num=7,
        novel_id=1,
    )

    assert rendered.text == "rendered:next_chapter:7"
    assert rendered.meta["prompt_asset_id"] == "generation.chapter.writer"
    assert rendered.meta["prompt_version"] == "v2"
    assert rendered.meta["prompt_template"] == "next_chapter"
    assert calls == [("next_chapter", {"chapter_num": 7, "novel_id": 1})]


def test_prompt_asset_lookup_rejects_unknown_asset():
    assert get_prompt_asset("missing.asset") is None
