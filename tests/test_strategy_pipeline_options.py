"""Tests for get_pipeline_options and get_max_retries."""
import pytest
from unittest.mock import patch
from app.core.strategy import get_pipeline_options, get_max_retries, DEFAULT_PIPELINE_OPTIONS


def test_defaults_when_no_pipeline_options_in_yaml():
    """web-novel.yaml has no pipeline_options → returns all defaults."""
    opts = get_pipeline_options("web-novel")
    assert opts["combined_reviewer"] is False
    assert opts["max_retries"] == 2
    assert opts["enable_cross_chapter_check"] is True
    assert opts["enable_refine_outline"] is True


def test_defaults_for_unknown_strategy():
    opts = get_pipeline_options("nonexistent-strategy-xyz")
    assert opts == DEFAULT_PIPELINE_OPTIONS


def test_get_max_retries_default():
    assert get_max_retries("web-novel") == 2


def test_pipeline_options_from_yaml(tmp_path, monkeypatch):
    """Strategy YAML with pipeline_options overrides defaults."""
    import yaml
    from app.core import strategy as strat_mod
    yaml_content = {
        "id": "test-fast",
        "pipeline_options": {
            "combined_reviewer": True,
            "max_retries": 1,
            "enable_cross_chapter_check": False,
            "enable_refine_outline": False,
        },
    }
    (tmp_path / "test-fast.yaml").write_text(yaml.dump(yaml_content))
    monkeypatch.setattr(strat_mod, "STRATEGIES_DIR", tmp_path)
    strat_mod.get_strategy_config.cache_clear()
    opts = get_pipeline_options("test-fast")
    assert opts["combined_reviewer"] is True
    assert opts["max_retries"] == 1
    assert opts["enable_cross_chapter_check"] is False
    assert opts["enable_refine_outline"] is False
    strat_mod.get_strategy_config.cache_clear()


def test_max_retries_from_yaml(tmp_path, monkeypatch):
    import yaml
    from app.core import strategy as strat_mod
    yaml_content = {"id": "test-r1", "pipeline_options": {"max_retries": 1}}
    (tmp_path / "test-r1.yaml").write_text(yaml.dump(yaml_content))
    monkeypatch.setattr(strat_mod, "STRATEGIES_DIR", tmp_path)
    strat_mod.get_strategy_config.cache_clear()
    assert get_max_retries("test-r1") == 1
    strat_mod.get_strategy_config.cache_clear()
