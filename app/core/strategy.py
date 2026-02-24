"""Strategy factory - load stage-to-model mapping from presets/strategies/*.yaml."""
from functools import lru_cache
from pathlib import Path
import yaml

from app.core.config import get_settings

STRATEGIES_DIR = Path(__file__).resolve().parents[2] / "presets" / "strategies"
DEFAULT_STAGES = {
    "architect": {"provider": "openai", "model": "gpt-4o-mini"},
    "outliner": {"provider": "openai", "model": "gpt-4o-mini"},
    "writer": {"provider": "openai", "model": "gpt-4o"},
    "reviewer": {"provider": "openai", "model": "gpt-4o-mini"},
    "finalizer": {"provider": "openai", "model": "gpt-4o-mini"},
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
    settings = get_settings()
    config = get_strategy_config(strategy_key)
    stages = config.get("stages") or DEFAULT_STAGES
    s = stages.get(stage) or DEFAULT_STAGES.get(stage)
    if isinstance(s, dict):
        provider = s.get("provider", settings.default_llm_provider or "openai")
        model = s.get("model", settings.default_llm_model or "gpt-4o-mini")
        if model in ("__default__", "default", "", None):
            model = settings.default_llm_model or "gpt-4o-mini"
        return provider, model
    return settings.default_llm_provider or "openai", settings.default_llm_model or "gpt-4o-mini"
