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


def _deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
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
        }
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    stages = _deep_merge_dicts(DEFAULT_STAGES, data.get("stages") or {})
    inference = _deep_merge_dicts(DEFAULT_INFERENCE, data.get("inference") or {})
    review_weights = _deep_merge_dicts(DEFAULT_REVIEW_WEIGHTS, data.get("review_weights") or {})
    return {**data, "stages": stages, "inference": inference, "review_weights": review_weights}


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


def get_review_weights(strategy_key: str | None) -> dict[str, float]:
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
    opts = config.get("pipeline_options") or {}
    return {
        "combined_reviewer": bool(opts.get("combined_reviewer", DEFAULT_PIPELINE_OPTIONS["combined_reviewer"])),
        "max_retries": int(opts.get("max_retries", DEFAULT_PIPELINE_OPTIONS["max_retries"])),
        "enable_cross_chapter_check": bool(opts.get("enable_cross_chapter_check", DEFAULT_PIPELINE_OPTIONS["enable_cross_chapter_check"])),
        "enable_refine_outline": bool(opts.get("enable_refine_outline", DEFAULT_PIPELINE_OPTIONS["enable_refine_outline"])),
    }


def get_max_retries(strategy_key: str | None) -> int:
    """Return max_retries for the given strategy."""
    return get_pipeline_options(strategy_key)["max_retries"]
