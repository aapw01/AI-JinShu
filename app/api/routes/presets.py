"""Presets routes - load from YAML config."""
from functools import lru_cache
from pathlib import Path
from fastapi import APIRouter, HTTPException
import yaml

router = APIRouter()

PRESETS_DIR = Path(__file__).resolve().parents[3] / "presets"


@lru_cache(maxsize=64)
def _load_yaml(path: str) -> list | dict:
    """Load YAML file with caching. Path must be string for lru_cache."""
    p = Path(path)
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f) or []


@lru_cache(maxsize=1)
def _load_all_presets() -> dict:
    """Load all presets with caching."""
    result = {}
    if not PRESETS_DIR.exists():
        return result
    for name in ["genres", "styles", "lengths", "languages", "audiences", "inspiration_tags"]:
        p = PRESETS_DIR / f"{name}.yaml"
        result[name] = _load_yaml(str(p))
    methods_dir = PRESETS_DIR / "methods"
    if methods_dir.exists():
        result["methods"] = {f.stem: _load_yaml(str(f)) for f in methods_dir.glob("*.yaml")}
    strategies_dir = PRESETS_DIR / "strategies"
    if strategies_dir.exists():
        result["strategies"] = {f.stem: _load_yaml(str(f)) for f in strategies_dir.glob("*.yaml")}
    anti = PRESETS_DIR / "anti_ai_rules.yaml"
    if anti.exists():
        result["anti_ai_rules"] = _load_yaml(str(anti))
    return result


@router.get("")
def list_presets():
    """List all preset categories and options."""
    return _load_all_presets()


@router.get("/{category}")
def get_preset_category(category: str):
    """Get presets for a category."""
    p = PRESETS_DIR / f"{category}.yaml"
    if p.exists():
        return _load_yaml(str(p))
    subdir = PRESETS_DIR / category
    if subdir.is_dir():
        return {f.stem: _load_yaml(str(f)) for f in subdir.glob("*.yaml")}
    raise HTTPException(404, f"Unknown category: {category}")
