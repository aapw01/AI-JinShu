"""Tests for finalize memory extraction fan-out."""

from __future__ import annotations

from types import SimpleNamespace
import threading
import time


def test_finalize_memory_extraction_tasks_run_independently_in_parallel(monkeypatch):
    from app.core.llm_usage import (
        begin_usage_session,
        end_usage_session,
        record_usage_from_response,
    )
    from app.services.generation.nodes import finalize as finalize_node
    from app.services.generation.nodes.finalize import _run_memory_extraction_tasks

    class _Tracker:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def hold(self):
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.04)
                record_usage_from_response(
                    SimpleNamespace(
                        usage_metadata={
                            "input_tokens": 2,
                            "output_tokens": 3,
                            "total_tokens": 5,
                        }
                    ),
                    stage="test-memory",
                )
            finally:
                with self.lock:
                    self.active -= 1

    tracker = _Tracker()
    captured: dict[str, object] = {}

    def _summary(*_args, **_kwargs):
        tracker.hold()
        return "摘要"

    class _FactExtractor:
        def run(self, **kwargs):
            tracker.hold()
            captured["fact_provider"] = kwargs["provider"]
            captured["fact_model"] = kwargs["model"]
            return {"events": [{"title": "事件"}]}

        def run_foreshadow_extraction(self, **_kwargs):
            tracker.hold()
            return {"planted": ["伏笔"], "resolved": ["回收"]}

        async def run_relation_extraction(self, **_kwargs):
            tracker.hold()

            class _Relations:
                relations = ["关系"]

            return _Relations()

    class _ProgressionExtractor:
        def run(self, **_kwargs):
            tracker.hold()
            return {
                "advancement": {"main": "推进"},
                "transition": {},
                "advancement_confidence": 0.9,
                "transition_confidence": 0.8,
                "validation_notes": [],
            }

    monkeypatch.setattr(finalize_node, "generate_chapter_summary", _summary)
    state = {
        "strategy": "web-novel",
        "target_language": "zh",
        "outline": {"title": "第1章"},
        "fact_extractor": _FactExtractor(),
        "progression_memory_extractor": _ProgressionExtractor(),
        "character_state": {"甲": {}},
    }

    begin_usage_session("memory-fanout-test")
    try:
        result = _run_memory_extraction_tasks(
            state=state,
            chapter_num=1,
            final_content="正文",
            fact_profile={
                "provider": "fast-provider",
                "model": "fast-fact",
                "inference": {},
            },
            progression_profile={
                "provider": "fast-provider",
                "model": "fast-memory",
                "inference": {},
            },
        )
        usage = end_usage_session()
    except Exception:
        end_usage_session()
        raise

    assert result["summary_text"] == "摘要"
    assert result["extracted_facts"]["events"][0]["title"] == "事件"
    assert result["progression_memory_raw"]["advancement"]["main"] == "推进"
    assert result["foreshadow_planted"] == ["伏笔"]
    assert result["foreshadow_resolved"] == ["回收"]
    assert result["relation_objects"] == ["关系"]
    assert captured == {"fact_provider": "fast-provider", "fact_model": "fast-fact"}
    assert tracker.max_active > 1
    assert usage["calls"] == 5
    assert usage["input_tokens"] == 10
    assert usage["output_tokens"] == 15
