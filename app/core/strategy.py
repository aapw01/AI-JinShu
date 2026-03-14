"""Strategy factory - load stage-to-model mapping from presets/strategies/*.yaml."""
from functools import lru_cache
from pathlib import Path
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


@lru_cache(maxsize=32)
def get_strategy_config(strategy_key: str | None) -> dict:
    """Load strategy YAML and return full config including stages. Results are cached."""
    if not strategy_key:
        strategy_key = "web-novel"
    path = STRATEGIES_DIR / f"{strategy_key}.yaml"
    if not path.exists():
        return {"stages": DEFAULT_STAGES}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    stages = data.get("stages") or DEFAULT_STAGES
    return {**data, "stages": stages}


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
