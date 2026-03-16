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
