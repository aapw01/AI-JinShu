from __future__ import annotations

from app.core import strategy


def test_get_inference_for_stage_returns_defaults():
    strategy.get_strategy_config.cache_clear()
    fact = strategy.get_inference_for_stage("web-novel", "fact_extractor")
    factual = strategy.get_inference_for_stage("web-novel", "reviewer.factual")
    structured = strategy.get_inference_for_stage("web-novel", "reviewer.structured")

    assert fact["temperature"] == 0.1
    assert fact["gemini"]["safety_settings"][0]["category"] == "HARM_CATEGORY_DANGEROUS_CONTENT"
    assert fact["gemini"]["safety_settings"][0]["threshold"] == "BLOCK_ONLY_HIGH"
    assert factual["temperature"] == 0.1
    assert structured["temperature"] == 0.2


def test_get_inference_for_stage_merges_base_and_exact(monkeypatch, tmp_path):
    strategies_dir = tmp_path / "strategies"
    strategies_dir.mkdir()
    (strategies_dir / "custom.yaml").write_text(
        """
inference:
  reviewer:
    temperature: 0.3
    gemini:
      safety_settings:
        - category: HARM_CATEGORY_DANGEROUS_CONTENT
          threshold: BLOCK_ONLY_HIGH
  reviewer.factual:
    temperature: 0.15
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(strategy, "STRATEGIES_DIR", strategies_dir)
    strategy.get_strategy_config.cache_clear()

    resolved = strategy.get_inference_for_stage("custom", "reviewer.factual")

    assert resolved["temperature"] == 0.15
    assert resolved["gemini"]["safety_settings"][0]["threshold"] == "BLOCK_ONLY_HIGH"


def test_get_review_weights_returns_defaults():
    strategy.get_strategy_config.cache_clear()

    weights = strategy.get_review_weights("web-novel")

    assert weights == {
        "structure": 0.28,
        "factual": 0.24,
        "progression": 0.28,
        "aesthetic": 0.20,
    }


def test_resolve_ai_profile_uses_novel_config_override():
    strategy.get_strategy_config.cache_clear()

    resolved = strategy.resolve_ai_profile(
        "web-novel",
        "fact_extractor",
        novel_config={
            "ai_profiles": {
                "fact_extractor": {
                    "model": "override-model",
                    "inference": {"temperature": 0.05},
                }
            }
        },
    )

    assert resolved["model"] == "override-model"
    assert resolved["inference"]["temperature"] == 0.05
    assert "novel_config" in resolved["resolution_trace"]


def test_resolve_ai_profile_ignores_gemini_settings_for_non_gemini_provider():
    strategy.get_strategy_config.cache_clear()

    resolved = strategy.resolve_ai_profile(
        "web-novel",
        "fact_extractor",
        runtime_override={"provider": "openai", "model": "compatible-model"},
    )

    assert resolved["provider"] == "openai"
    assert resolved["model"] == "compatible-model"
    assert "gemini" not in resolved["inference"]


def test_node_prewrite_does_not_force_constitution_provider_into_other_assets(monkeypatch):
    from app.services.generation.nodes.init_node import node_prewrite

    captured: dict[str, object] = {}

    class _PrewriteAgent:
        def run(self, novel, num_chapters, language="zh", provider=None, model=None, strategy_key=None, novel_config=None):
            captured["provider"] = provider
            captured["model"] = model
            captured["strategy_key"] = strategy_key
            captured["novel_config"] = novel_config
            return {"constitution": {}, "specification": {"characters": []}, "creative_plan": {}, "tasks": {}}

    monkeypatch.setattr("app.services.generation.nodes.init_node._is_resume_like", lambda _state: False)
    monkeypatch.setattr("app.services.generation.nodes.init_node.progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.generation.nodes.init_node.save_prewrite_artifacts", lambda *_args, **_kwargs: None)

    state = {
        "novel_id": 1,
        "strategy": "web-novel",
        "num_chapters": 20,
        "target_language": "zh",
        "novel_info": {"title": "测试书", "config": {"ai_profiles": {"prewrite.specification": {"provider": "openai", "model": "x"}}}},
        "prewrite_agent": _PrewriteAgent(),
    }

    result = node_prewrite(state)  # type: ignore[arg-type]

    assert result["prewrite"]["specification"] == {"characters": []}
    assert captured["provider"] is None
    assert captured["model"] is None
    assert captured["strategy_key"] == "web-novel"
    assert isinstance(captured["novel_config"], dict)
