"""Lightweight prompt asset registry.

This layer gives production prompts stable IDs and versions while keeping the
existing Jinja2 template system intact. It is intentionally small: prompt text
still lives in `app/prompts/templates`, and callers can migrate one prompt at a
time without changing business flow.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.prompts import render_prompt


@dataclass(frozen=True)
class PromptContextPolicy:
    """Context budget hints attached to a prompt asset."""

    required: tuple[str, ...] = ()
    preferred: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    max_tokens_budget: int | None = None


@dataclass(frozen=True)
class PromptAsset:
    """Registered prompt metadata used for tracing, tests, and gradual migration."""

    id: str
    version: str
    task_type: str
    template_name: str
    mode: str = "text"
    language: str = "zh"
    output_contract: str | None = None
    context_policy: PromptContextPolicy = field(default_factory=PromptContextPolicy)

    @property
    def key(self) -> str:
        """Return the stable registry key `id@version`."""
        return f"{self.id}@{self.version}"


@dataclass(frozen=True)
class RenderedPrompt:
    """Rendered prompt text plus stable metadata for downstream LLM calls."""

    text: str
    asset: PromptAsset
    meta: dict[str, Any]


_ASSETS: tuple[PromptAsset, ...] = (
    PromptAsset(
        id="generation.outline.volume_batch",
        version="v1",
        task_type="outliner",
        template_name="volume_outline_batch",
        mode="structured",
        output_contract="VolumeOutlineBatchSchema",
        context_policy=PromptContextPolicy(
            required=("prewrite", "chapter_range"),
            preferred=("planning_context", "previous_summaries"),
            max_tokens_budget=6000,
        ),
    ),
    PromptAsset(
        id="generation.chapter.first_writer",
        version="v2",
        task_type="writer",
        template_name="first_chapter",
        mode="structured",
        output_contract="ChapterBodySchema",
        context_policy=PromptContextPolicy(
            required=("outline_contract", "global_bible", "style_overlay"),
            preferred=("character_focus_pack", "story_bible_context", "thread_ledger", "recent_window"),
            optional=("knowledge_chunks", "volume_brief"),
            max_tokens_budget=8000,
        ),
    ),
    PromptAsset(
        id="generation.chapter.writer",
        version="v2",
        task_type="writer",
        template_name="next_chapter",
        mode="structured",
        output_contract="ChapterBodySchema",
        context_policy=PromptContextPolicy(
            required=("outline_contract", "global_bible", "previous_transition_state", "style_overlay"),
            preferred=("character_focus_pack", "story_bible_context", "thread_ledger", "recent_window", "anti_repeat_constraints"),
            optional=("knowledge_chunks", "volume_brief", "book_progression_state"),
            max_tokens_budget=8000,
        ),
    ),
    PromptAsset(
        id="generation.chapter.finalizer",
        version="v2",
        task_type="finalizer",
        template_name="finalizer_polish",
        mode="structured",
        output_contract="ChapterBodySchema",
        context_policy=PromptContextPolicy(
            required=("draft", "feedback", "style_overlay"),
            preferred=("memory_policy",),
            max_tokens_budget=6000,
        ),
    ),
    PromptAsset(
        id="generation.review.structured",
        version="v2",
        task_type="reviewer",
        template_name="reviewer_structured",
        mode="structured",
        output_contract="ReviewScorecardSchema",
        context_policy=PromptContextPolicy(required=("draft",), preferred=("style_profile",), max_tokens_budget=7000),
    ),
    PromptAsset(
        id="generation.review.factual",
        version="v2",
        task_type="reviewer",
        template_name="reviewer_factual_structured",
        mode="structured",
        output_contract="ReviewScorecardSchema",
        context_policy=PromptContextPolicy(required=("draft", "context_json"), max_tokens_budget=7000),
    ),
    PromptAsset(
        id="generation.review.progression",
        version="v1",
        task_type="reviewer",
        template_name="reviewer_progression_structured",
        mode="structured",
        output_contract="ProgressionReviewSchema",
        context_policy=PromptContextPolicy(required=("draft", "progression_context_json"), max_tokens_budget=7000),
    ),
    PromptAsset(
        id="generation.review.aesthetic",
        version="v2",
        task_type="reviewer",
        template_name="reviewer_aesthetic_structured",
        mode="structured",
        output_contract="ReviewScorecardSchema",
        context_policy=PromptContextPolicy(required=("draft",), max_tokens_budget=6000),
    ),
    PromptAsset(
        id="generation.review.combined",
        version="v1",
        task_type="reviewer",
        template_name="reviewer_combined",
        mode="structured",
        output_contract="ReviewCombinedSchema",
        context_policy=PromptContextPolicy(
            required=("draft", "context_json", "progression_context_json"),
            preferred=("style_profile", "memory_policy"),
            max_tokens_budget=9000,
        ),
    ),
)

_BY_KEY = {asset.key: asset for asset in _ASSETS}
_LATEST_BY_ID: dict[str, PromptAsset] = {}
for _asset in _ASSETS:
    _LATEST_BY_ID[_asset.id] = _asset


def list_prompt_assets() -> list[PromptAsset]:
    """Return all registered prompt assets in deterministic order."""
    return list(_ASSETS)


def get_prompt_asset(asset_id: str, version: str | None = None) -> PromptAsset | None:
    """Look up a prompt asset by ID and optional version."""
    key = f"{asset_id}@{version}" if version else None
    if key:
        return _BY_KEY.get(key)
    return _LATEST_BY_ID.get(asset_id)


def render_prompt_asset(
    asset_id: str,
    version: str | None = None,
    *,
    renderer: Callable[..., str] | None = None,
    **kwargs: Any,
) -> RenderedPrompt:
    """Render a registered prompt and return text plus trace metadata."""
    asset = get_prompt_asset(asset_id, version)
    if asset is None:
        suffix = f"@{version}" if version else ""
        raise KeyError(f"Unknown prompt asset: {asset_id}{suffix}")
    render = renderer or render_prompt
    text = render(asset.template_name, **kwargs)
    return RenderedPrompt(
        text=text,
        asset=asset,
        meta={
            "prompt_asset_id": asset.id,
            "prompt_version": asset.version,
            "prompt_template": asset.template_name,
            "prompt_task_type": asset.task_type,
            "prompt_mode": asset.mode,
            "prompt_output_contract": asset.output_contract,
        },
    )
