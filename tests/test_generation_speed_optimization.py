"""Tests for generation speed optimization: combined reviewer + pipeline_options."""

import threading
import time
from types import SimpleNamespace

import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# ReviewerAgent.run_combined
# ---------------------------------------------------------------------------


class TestRunCombined:
    def _make_agent(self):
        from app.services.generation.agents import ReviewerAgent

        return ReviewerAgent()

    def _make_combined_result(self):
        return {
            "structure": {
                "score": 0.85,
                "confidence": 0.8,
                "feedback": "ok",
                "positives": [],
                "must_fix": [],
                "should_fix": [],
                "risks": [],
            },
            "factual": {
                "score": 0.9,
                "confidence": 0.85,
                "feedback": "ok",
                "contradictions": ["矛盾1"],
                "must_fix": [],
                "should_fix": [],
                "risks": [],
            },
            "progression": {
                "score": 0.82,
                "confidence": 0.75,
                "feedback": "ok",
                "must_fix": [],
                "should_fix": [],
                "risks": [],
                "duplicate_beats": [],
                "no_new_delta": [],
                "repeated_reveal": [],
                "repeated_relationship_turn": [],
                "transition_conflict": [],
            },
            "aesthetic": {
                "score": 0.83,
                "confidence": 0.78,
                "feedback": "ok",
                "positives": ["亮点"],
                "must_fix": [],
                "should_fix": [],
                "risks": [],
            },
        }

    def test_run_combined_returns_four_dicts(self):
        """run_combined returns exactly 6 dicts in the right order."""
        agent = self._make_agent()
        combined_result = self._make_combined_result()
        with patch(
            "app.services.generation.agents._invoke_json_with_schema",
            return_value=combined_result,
        ):
            struct_raw, factual_raw, prog_raw, aes_raw, ai_flavor_raw, webnovel_raw = (
                agent.run_combined(
                    draft="测试章节内容",
                    chapter_num=1,
                    context={},
                    language="zh",
                )
            )
        assert isinstance(struct_raw, dict)
        assert isinstance(factual_raw, dict)
        assert isinstance(prog_raw, dict)
        assert isinstance(aes_raw, dict)
        assert isinstance(ai_flavor_raw, dict)
        assert isinstance(webnovel_raw, dict)
        assert struct_raw["score"] == pytest.approx(0.85)
        assert factual_raw["contradictions"] == ["矛盾1"]

    def test_run_combined_fallback_on_error(self):
        """run_combined returns 6 empty dicts when LLM fails, never raises."""
        agent = self._make_agent()
        with patch(
            "app.services.generation.agents._invoke_json_with_schema",
            side_effect=RuntimeError("LLM error"),
        ):
            result = agent.run_combined(draft="x", chapter_num=1, context={})
        assert len(result) == 6
        for d in result:
            assert isinstance(d, dict)

    def test_run_combined_clamps_score_above_1(self):
        """Scores > 1 are clamped to [0, 1]."""
        agent = self._make_agent()
        combined_result = self._make_combined_result()
        combined_result["structure"]["score"] = 85.0  # out of 100
        with patch(
            "app.services.generation.agents._invoke_json_with_schema",
            return_value=combined_result,
        ):
            struct_raw, *_ = agent.run_combined(draft="x", chapter_num=1, context={})
        assert struct_raw["score"] == pytest.approx(0.85)

    def test_run_combined_reconstructs_contradictions_from_must_fix(self):
        """If factual.contradictions is empty, rebuild from factual.must_fix[].claim."""
        agent = self._make_agent()
        combined_result = self._make_combined_result()
        combined_result["factual"]["contradictions"] = []
        combined_result["factual"]["must_fix"] = [
            {
                "category": "identity",
                "severity": "must_fix",
                "claim": "角色身份冲突",
                "evidence": "原文",
                "confidence": 0.9,
            }
        ]
        with patch(
            "app.services.generation.agents._invoke_json_with_schema",
            return_value=combined_result,
        ):
            _, factual_raw, *_ = agent.run_combined(
                draft="x", chapter_num=1, context={}
            )
        assert "角色身份冲突" in factual_raw["contradictions"]

    def test_run_combined_aesthetic_highlights_from_positives(self):
        """aesthetic.highlights is populated from aesthetic.positives for downstream compat."""
        agent = self._make_agent()
        combined_result = self._make_combined_result()
        combined_result["aesthetic"]["positives"] = ["情绪爆发很好"]
        combined_result["aesthetic"].pop("highlights", None)
        with patch(
            "app.services.generation.agents._invoke_json_with_schema",
            return_value=combined_result,
        ):
            struct_raw, factual_raw, prog_raw, aes_raw, ai_flavor_raw, webnovel_raw = (
                agent.run_combined(draft="x", chapter_num=1, context={})
            )
        assert aes_raw["highlights"] == ["情绪爆发很好"]


# ---------------------------------------------------------------------------
# pipeline_options switches
# ---------------------------------------------------------------------------


class TestPipelineOptionSwitches:
    def test_cross_chapter_check_skipped_when_disabled(self):
        """node_cross_chapter_check returns {} immediately when enable_cross_chapter_check=False."""
        from app.services.generation.nodes.cross_chapter_check import (
            node_cross_chapter_check,
        )

        state = MagicMock()
        state.get = lambda k, d=None: {
            "strategy": "fast-local",
            "current_chapter": 5,
            "draft": "some text",
        }.get(k, d)
        state.__getitem__ = lambda self, k: {"current_chapter": 5}[k]
        with patch(
            "app.core.strategy.get_pipeline_options",
            return_value={
                "enable_cross_chapter_check": False,
                "combined_reviewer": True,
                "max_retries": 1,
                "enable_refine_outline": False,
            },
        ):
            result = node_cross_chapter_check(state)
        assert result == {}

    def test_refine_outline_skipped_when_disabled(self):
        """node_refine_chapter_outline returns original outline immediately when enable_refine_outline=False."""
        from app.services.generation.nodes.chapter_loop import (
            node_refine_chapter_outline,
        )

        original_outline = {
            "chapter_num": 5,
            "title": "第五章",
            "outline": "主角遇到敌人",
        }
        state = MagicMock()
        state.get = lambda k, d=None: {
            "strategy": "fast-local",
            "current_chapter": 5,
            "outline": original_outline,
        }.get(k, d)
        with patch(
            "app.core.strategy.get_pipeline_options",
            return_value={
                "enable_refine_outline": False,
                "combined_reviewer": True,
                "max_retries": 1,
                "enable_cross_chapter_check": False,
            },
        ):
            result = node_refine_chapter_outline(state)
        assert result["outline"] == original_outline

    def test_route_review_uses_strategy_max_retries(self):
        """_route_review respects max_retries from strategy (e.g. 1 instead of default 2)."""
        from app.services.generation.graph import _route_review

        state = {
            "score": 0.5,  # below threshold
            "review_attempt": 1,  # would still retry with default MAX_RETRIES=2
            "rerun_count": 0,
            "strategy": "fast-local",
            "review_gate": {},
        }
        with patch("app.core.strategy.get_max_retries", return_value=1):
            route = _route_review(state)
        # review_attempt=1, max_retries=1, so 1 < 1 is False → rollback_rerun
        assert route == "rollback_rerun"


class TestReviewerParallelism:
    def test_legacy_four_way_reviewer_runs_independent_checks_concurrently(
        self, monkeypatch
    ):
        """The non-combined reviewer path should fan out independent review calls."""
        from app.core.llm_usage import (
            begin_usage_session,
            end_usage_session,
            record_usage_from_response,
        )
        from app.services.generation.nodes.review import node_review

        class _Reviewer:
            def __init__(self):
                self.active = 0
                self.max_active = 0
                self.lock = threading.Lock()

            def _enter(self):
                with self.lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)

            def _leave(self):
                with self.lock:
                    self.active -= 1

            def _call(self, score=0.8, **extra):
                self._enter()
                try:
                    time.sleep(0.04)
                    record_usage_from_response(
                        SimpleNamespace(
                            usage_metadata={
                                "input_tokens": 7,
                                "output_tokens": 11,
                                "total_tokens": 18,
                            }
                        ),
                        stage="test-review",
                    )
                    return {
                        "score": score,
                        "confidence": 0.9,
                        "feedback": "ok",
                        "must_fix": [],
                        "should_fix": [],
                        "positives": [],
                        "risks": [],
                        **extra,
                    }
                finally:
                    self._leave()

            def run_structured(self, *_args, **_kwargs):
                return self._call(0.8)

            def run_factual_structured(self, *_args, **_kwargs):
                return self._call(0.8, contradictions=[])

            def run_progression_structured(self, *_args, **_kwargs):
                return self._call(
                    0.8,
                    duplicate_beats=[],
                    no_new_delta=[],
                    repeated_reveal=[],
                    repeated_relationship_turn=[],
                    transition_conflict=[],
                )

            def run_aesthetic_structured(self, *_args, **_kwargs):
                return self._call(0.8)

        reviewer = _Reviewer()
        monkeypatch.setattr(
            "app.services.generation.nodes.review.progress",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            "app.services.generation.nodes.review.resolve_ai_profile",
            lambda *_args, **_kwargs: {
                "provider": "openai",
                "model": "mock",
                "inference": {},
            },
        )
        with patch(
            "app.core.strategy.get_pipeline_options",
            return_value={
                "combined_reviewer": False,
                "max_retries": 2,
                "enable_cross_chapter_check": True,
                "enable_refine_outline": True,
            },
        ):
            begin_usage_session("reviewer-fanout-test")
            try:
                result = node_review(
                    {
                        "current_chapter": 1,
                        "num_chapters": 1,
                        "strategy": "web-novel",
                        "target_language": "zh",
                        "native_style_profile": "默认",
                        "context": {},
                        "draft": "正文",
                        "reviewer": reviewer,
                        "novel_info": {"config": {}},
                    }
                )
                usage = end_usage_session()
            except Exception:
                end_usage_session()
                raise

        assert result["score"] == pytest.approx(0.8)
        assert reviewer.max_active > 1
        assert usage["calls"] == 4
        assert usage["input_tokens"] == 28
        assert usage["output_tokens"] == 44

    def test_combined_reviewer_uses_combined_profile(self, monkeypatch):
        from app.services.generation.nodes.review import node_review

        captured: dict[str, object] = {}

        class _Reviewer:
            def run_combined(
                self,
                draft,
                chapter_num,
                context,
                language,
                native_style_profile,
                provider,
                model,
                inference=None,
            ):
                captured["provider"] = provider
                captured["model"] = model
                captured["inference"] = inference
                payload = {
                    "score": 0.8,
                    "confidence": 0.9,
                    "feedback": "ok",
                    "must_fix": [],
                    "should_fix": [],
                    "positives": [],
                    "risks": [],
                }
                progression = {
                    **payload,
                    "duplicate_beats": [],
                    "no_new_delta": [],
                    "repeated_reveal": [],
                    "repeated_relationship_turn": [],
                    "transition_conflict": [],
                }
                factual = {**payload, "contradictions": []}
                return payload, factual, progression, payload, {}, {}

        def _profile(_strategy, asset_key, **_kwargs):
            if asset_key == "reviewer.combined":
                return {
                    "provider": "fast",
                    "model": "combined",
                    "inference": {"temperature": 0.18},
                }
            return {"provider": "slow", "model": asset_key, "inference": {}}

        monkeypatch.setattr(
            "app.services.generation.nodes.review.progress",
            lambda *_args, **_kwargs: None,
        )
        monkeypatch.setattr(
            "app.services.generation.nodes.review.resolve_ai_profile", _profile
        )
        with patch(
            "app.core.strategy.get_pipeline_options",
            return_value={
                "combined_reviewer": True,
                "max_retries": 1,
                "enable_cross_chapter_check": True,
                "enable_refine_outline": False,
            },
        ):
            node_review(
                {
                    "current_chapter": 1,
                    "num_chapters": 1,
                    "strategy": "fast-pro",
                    "target_language": "zh",
                    "native_style_profile": "默认",
                    "context": {},
                    "draft": "正文",
                    "reviewer": _Reviewer(),
                    "novel_info": {"config": {}},
                }
            )

        assert captured == {
            "provider": "fast",
            "model": "combined",
            "inference": {"temperature": 0.18},
        }


# ---------------------------------------------------------------------------
# fast-local.yaml preset
# ---------------------------------------------------------------------------


class TestFastLocalPreset:
    def test_fast_local_pipeline_options(self):
        """fast-local.yaml has all 4 speed options set correctly."""
        from app.core.strategy import get_pipeline_options, get_strategy_config

        get_strategy_config.cache_clear()
        opts = get_pipeline_options("fast-local")
        assert opts["combined_reviewer"] is True
        assert opts["max_retries"] == 1
        assert opts["enable_cross_chapter_check"] is False
        assert opts["enable_refine_outline"] is False

    def test_web_novel_defaults_unchanged(self):
        """web-novel strategy still uses default (non-fast) settings."""
        from app.core.strategy import get_pipeline_options, get_strategy_config

        get_strategy_config.cache_clear()
        opts = get_pipeline_options("web-novel")
        assert opts["combined_reviewer"] is False
        assert opts["max_retries"] == 2
        assert opts["enable_cross_chapter_check"] is True
        assert opts["enable_refine_outline"] is True
