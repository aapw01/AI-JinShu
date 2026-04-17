"""Strategy factory - load stage-to-model mapping from presets/strategies/*.yaml."""
from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.services.system_settings.runtime import get_primary_chat_runtime

STRATEGIES_DIR = Path(__file__).resolve().parents[2] / "presets" / "strategies"
DEFAULT_STAGES = {
    "architect": {"provider": "__default__", "model": "__default__"},
    "outliner": {"provider": "__default__", "model": "__default__"},
    "writer": {"provider": "__default__", "model": "__default__"},
    "reviewer": {"provider": "__default__", "model": "__default__"},
    "finalizer": {"provider": "__default__", "model": "__default__"},
}
DEFAULT_INFERENCE = {
    "fact_extractor": {
        "temperature": 0.1,
        "gemini": {
            "safety_settings": [
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_ONLY_HIGH",
                }
            ]
        },
    },
    "progression_memory": {"temperature": 0.1},
    "reviewer.structured": {"temperature": 0.2},
    "reviewer.factual": {"temperature": 0.1},
    "reviewer.progression": {"temperature": 0.15},
    "reviewer.aesthetic": {"temperature": 0.2},
    "reviewer.book": {"temperature": 0.2},
}
DEFAULT_REVIEW_WEIGHTS = {
    "structure": 0.28,
    "factual": 0.24,
    "progression": 0.28,
    "aesthetic": 0.20,
}
DEFAULT_ASSET_PROFILES: dict[str, dict[str, Any]] = {
    "prewrite.constitution": {"stage": "architect"},
    "prewrite.specification": {"stage": "architect"},
    "prewrite.storyline": {"stage": "architect"},
    "prewrite.blueprint": {"stage": "architect"},
    "outline.batch": {"stage": "outliner"},
    "outline.harmonize": {"stage": "outliner"},
    "writer": {"stage": "writer"},
    "reviewer.structured": {"stage": "reviewer.structured"},
    "reviewer.factual": {"stage": "reviewer.factual"},
    "reviewer.progression": {"stage": "reviewer.progression"},
    "reviewer.aesthetic": {"stage": "reviewer.aesthetic"},
    "reviewer.book": {"stage": "reviewer.book"},
    "finalizer": {"stage": "finalizer"},
    "fact_extractor": {"stage": "fact_extractor"},
    "progression_memory": {"stage": "progression_memory"},
    "rewrite": {"stage": "finalizer"},
}


def _deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """执行 deep merge dicts 相关辅助逻辑。"""
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


@lru_cache(maxsize=32)
def get_strategy_config(strategy_key: str | None) -> dict:
    """Load strategy YAML and return full config including stages. Results are cached."""
    if not strategy_key:
        strategy_key = "web-novel"
    path = STRATEGIES_DIR / f"{strategy_key}.yaml"
    if not path.exists():
        return {
            "stages": deepcopy(DEFAULT_STAGES),
            "inference": deepcopy(DEFAULT_INFERENCE),
            "review_weights": deepcopy(DEFAULT_REVIEW_WEIGHTS),
            "asset_profiles": deepcopy(DEFAULT_ASSET_PROFILES),
        }
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    stages = _deep_merge_dicts(DEFAULT_STAGES, data.get("stages") or {})
    inference = _deep_merge_dicts(DEFAULT_INFERENCE, data.get("inference") or {})
    review_weights = _deep_merge_dicts(DEFAULT_REVIEW_WEIGHTS, data.get("review_weights") or {})
    asset_profiles = _deep_merge_dicts(DEFAULT_ASSET_PROFILES, data.get("asset_profiles") or {})
    return {**data, "stages": stages, "inference": inference, "review_weights": review_weights, "asset_profiles": asset_profiles}


def get_model_for_stage(strategy_key: str | None, stage: str) -> tuple[str, str]:
    """Return (provider, model) for given strategy and stage."""
    primary = get_primary_chat_runtime()
    default_provider = str(primary.get("provider") or "openai")
    default_model = str(primary.get("model") or "gpt-4o-mini")
    config = get_strategy_config(strategy_key)
    stages = config.get("stages") or DEFAULT_STAGES
    s = stages.get(stage) or DEFAULT_STAGES.get(stage)
    if isinstance(s, dict):
        provider = default_provider
        model = s.get("model", default_model)
        if model in ("__default__", "default", "", None):
            model = default_model
        return provider, model
    return default_provider, default_model


def get_inference_for_stage(strategy_key: str | None, stage: str) -> dict[str, Any]:
    """Return stage-specific inference parameters for the resolved strategy."""
    config = get_strategy_config(strategy_key)
    inference = config.get("inference") or {}
    resolved: dict[str, Any] = {}
    base_stage = stage.split(".", 1)[0]
    if base_stage != stage:
        base_cfg = inference.get(base_stage)
        if isinstance(base_cfg, dict):
            resolved = _deep_merge_dicts(resolved, base_cfg)
    stage_cfg = inference.get(stage)
    if isinstance(stage_cfg, dict):
        resolved = _deep_merge_dicts(resolved, stage_cfg)
    return resolved


def get_asset_profile_config(strategy_key: str | None, asset_key: str) -> dict[str, Any]:
    """返回asset画像config。"""
    config = get_strategy_config(strategy_key)
    asset_profiles = config.get("asset_profiles") or {}
    raw = asset_profiles.get(asset_key)
    return deepcopy(raw) if isinstance(raw, dict) else deepcopy(DEFAULT_ASSET_PROFILES.get(asset_key) or {})


def _filter_inference_for_provider(inference: dict[str, Any], provider: str | None) -> dict[str, Any]:
    """执行 filter inference for provider 相关辅助逻辑。"""
    resolved = deepcopy(inference or {})
    provider_key = str(provider or "").strip().lower()
    if provider_key != "gemini":
        resolved.pop("gemini", None)
    return resolved


def resolve_ai_profile(
    strategy_key: str | None,
    asset_key: str,
    *,
    novel_config: dict[str, Any] | None = None,
    runtime_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """综合默认策略、资产画像、小说级覆盖和运行时覆盖，返回最终 AI 配置。"""
    asset_profile = get_asset_profile_config(strategy_key, asset_key)
    stage = str(asset_profile.get("stage") or asset_key)
    provider, model = get_model_for_stage(strategy_key, stage)
    inference = get_inference_for_stage(strategy_key, stage)
    resolution_trace: list[str] = ["defaults", "strategy"]

    if isinstance(asset_profile.get("inference"), dict):
        inference = _deep_merge_dicts(inference, asset_profile["inference"])
        resolution_trace.append("asset_profile")
    if asset_profile.get("provider"):
        provider = str(asset_profile["provider"])
        resolution_trace.append("asset_profile")
    if asset_profile.get("model"):
        model = str(asset_profile["model"])
        resolution_trace.append("asset_profile")

    ai_profiles = {}
    if isinstance(novel_config, dict):
        ai_profiles = novel_config.get("ai_profiles") or {}
    novel_override = ai_profiles.get(asset_key) if isinstance(ai_profiles, dict) else None
    if isinstance(novel_override, dict):
        if novel_override.get("provider"):
            provider = str(novel_override["provider"])
        if novel_override.get("model"):
            model = str(novel_override["model"])
        if isinstance(novel_override.get("inference"), dict):
            inference = _deep_merge_dicts(inference, novel_override["inference"])
        resolution_trace.append("novel_config")

    if isinstance(runtime_override, dict):
        if runtime_override.get("provider"):
            provider = str(runtime_override["provider"])
        if runtime_override.get("model"):
            model = str(runtime_override["model"])
        if isinstance(runtime_override.get("inference"), dict):
            inference = _deep_merge_dicts(inference, runtime_override["inference"])
        resolution_trace.append("runtime_override")

    filtered_inference = _filter_inference_for_provider(inference, provider)
    return {
        "asset_key": asset_key,
        "stage": stage,
        "provider": provider,
        "model": model,
        "inference": filtered_inference,
        "resolution_trace": resolution_trace,
    }


def get_review_weights(strategy_key: str | None) -> dict[str, float]:
    """返回审校weights。"""
    config = get_strategy_config(strategy_key)
    weights = config.get("review_weights") or {}
    return {
        "structure": float(weights.get("structure", DEFAULT_REVIEW_WEIGHTS["structure"]) or DEFAULT_REVIEW_WEIGHTS["structure"]),
        "factual": float(weights.get("factual", DEFAULT_REVIEW_WEIGHTS["factual"]) or DEFAULT_REVIEW_WEIGHTS["factual"]),
        "progression": float(weights.get("progression", DEFAULT_REVIEW_WEIGHTS["progression"]) or DEFAULT_REVIEW_WEIGHTS["progression"]),
        "aesthetic": float(weights.get("aesthetic", DEFAULT_REVIEW_WEIGHTS["aesthetic"]) or DEFAULT_REVIEW_WEIGHTS["aesthetic"]),
    }


DEFAULT_PIPELINE_OPTIONS: dict[str, Any] = {
    "combined_reviewer": False,
    "max_retries": 2,
    "enable_cross_chapter_check": True,
    "enable_refine_outline": True,
}


def get_pipeline_options(strategy_key: str | None) -> dict[str, Any]:
    """Return pipeline_options for the given strategy, merged over defaults."""
    config = get_strategy_config(strategy_key)
    raw_opts = config.get("pipeline_options")
    opts: dict = raw_opts if isinstance(raw_opts, dict) else {}
    defs = DEFAULT_PIPELINE_OPTIONS
    return {
        "combined_reviewer": bool(opts.get("combined_reviewer", defs["combined_reviewer"])),
        "max_retries": int(opts["max_retries"] if "max_retries" in opts else defs["max_retries"]),
        "enable_cross_chapter_check": bool(opts.get("enable_cross_chapter_check", defs["enable_cross_chapter_check"])),
        "enable_refine_outline": bool(opts.get("enable_refine_outline", defs["enable_refine_outline"])),
    }


def get_max_retries(strategy_key: str | None) -> int:
    """Return max_retries for the given strategy."""
    return get_pipeline_options(strategy_key)["max_retries"]
