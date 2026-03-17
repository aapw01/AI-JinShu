from __future__ import annotations

import json

import pytest

from app.services.generation.agents import FactExtractorAgent, ReviewerAgent
from app.services.generation.contracts import OutputContractError


class _Resp:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def invoke(self, _prompt: str):
        return _Resp("not-a-json-payload")


class _BlockedResp:
    def __init__(self):
        self.content = ""
        self.response_metadata = {
            "promptFeedback": {
                "blockReason": "PROHIBITED_CONTENT",
                "blockReasonMessage": "blocked by provider",
            }
        }


class _BlockedLLM:
    def invoke(self, _prompt: str):
        return _BlockedResp()


def test_reviewer_structured_parse_failure_raises_typed_error(monkeypatch):
    monkeypatch.setattr("app.services.generation.agents.get_llm_with_fallback", lambda *_args, **_kwargs: _FakeLLM())
    reviewer = ReviewerAgent()
    with pytest.raises(OutputContractError) as exc_info:
        reviewer.run_structured(
            draft="正文",
            chapter_num=1,
            language="zh",
            native_style_profile="默认",
            provider="openai",
            model="test-model",
        )
    assert exc_info.value.code in {"MODEL_OUTPUT_PARSE_FAILED", "MODEL_OUTPUT_SCHEMA_INVALID", "MODEL_OUTPUT_CONTRACT_EXHAUSTED"}
    assert exc_info.value.stage == "reviewer.structured"


def test_fact_extractor_parse_failure_raises_typed_error(monkeypatch):
    monkeypatch.setattr("app.services.generation.agents.get_llm_with_fallback", lambda *_args, **_kwargs: _FakeLLM())
    agent = FactExtractorAgent()
    with pytest.raises(OutputContractError) as exc_info:
        agent.run(
            chapter_num=1,
            content="正文",
            outline={"title": "t"},
            language="zh",
            provider="openai",
            model="test-model",
        )
    assert exc_info.value.code in {"MODEL_OUTPUT_PARSE_FAILED", "MODEL_OUTPUT_SCHEMA_INVALID", "MODEL_OUTPUT_CONTRACT_EXHAUSTED"}
    assert exc_info.value.stage == "fact_extractor"


def test_fact_extractor_provider_block_raises_typed_error(monkeypatch):
    monkeypatch.setattr("app.services.generation.agents.get_llm_with_fallback", lambda *_args, **_kwargs: _BlockedLLM())
    agent = FactExtractorAgent()
    with pytest.raises(OutputContractError) as exc_info:
        agent.run(
            chapter_num=25,
            content="正文",
            outline={"title": "t"},
            language="zh",
            provider="gemini",
            model="gemini-3-flash-preview",
        )
    assert exc_info.value.code == "MODEL_PROVIDER_BLOCKED"
    assert exc_info.value.stage == "fact_extractor"
    assert "PROHIBITED_CONTENT" in str(exc_info.value)


def test_reviewer_structured_passes_inference_to_llm(monkeypatch):
    captured: dict[str, object] = {}

    class _StructuredLLM:
        def invoke(self, _prompt: str):
            return _Resp('{"score":0.8,"confidence":0.9,"feedback":"ok","positives":[],"must_fix":[],"should_fix":[],"risks":[]}')

    def _fake_get_llm(_provider=None, _model=None, *, inference=None):
        captured["inference"] = inference
        return _StructuredLLM()

    monkeypatch.setattr("app.services.generation.agents.get_llm_with_fallback", _fake_get_llm)
    reviewer = ReviewerAgent()
    reviewer.run_structured(
        draft="正文",
        chapter_num=1,
        provider="openai",
        model="gpt-4o-mini",
        inference={"temperature": 0.2},
    )
    assert captured["inference"] == {"temperature": 0.2}


def test_reviewer_progression_context_json_is_complete_json(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_render_prompt(_template: str, **kwargs):
        captured["context_json"] = kwargs["context_json"]
        return "prompt"

    monkeypatch.setattr("app.services.generation.agents.render_prompt", _fake_render_prompt)
    monkeypatch.setattr("app.services.generation.agents.get_llm_with_fallback", lambda *_args, **_kwargs: _FakeLLM())
    monkeypatch.setattr(
        "app.services.generation.agents._invoke_json_with_schema",
        lambda *_args, **_kwargs: {
            "score": 0.8,
            "confidence": 0.8,
            "feedback": "ok",
            "positives": [],
            "must_fix": [],
            "should_fix": [],
            "risks": [],
            "duplicate_beats": [],
            "no_new_delta": [],
            "repeated_reveal": [],
            "repeated_relationship_turn": [],
            "transition_conflict": [],
        },
    )
    reviewer = ReviewerAgent()
    reviewer.run_progression_structured(
        draft="正文",
        chapter_num=2,
        context={
            "outline_contract": {"chapter_objective": "推进主线调查"},
            "recent_advancement_window": [{"chapter_objective": "揭示真相", "actual_progress": "推进了关系变化" * 100}],
            "previous_transition_state": {"ending_scene": "别墅门外", "last_action": "摔门而出"},
            "current_volume_arc_state": {"forbidden_repeats": ["不要重复认亲"]},
            "book_progression_state": {"major_beats": ["认亲", "打脸", "揭秘"]},
            "anti_repeat_constraints": {"recent_objectives": ["揭示真相" * 300]},
            "transition_constraints": {"opening_constraints": ["必须显式交代过渡"]},
        },
        provider="openai",
        model="gpt-4o-mini",
    )

    parsed = json.loads(str(captured["context_json"]))
    assert parsed["outline_contract"]["chapter_objective"] == "推进主线调查"
    assert parsed["previous_transition_state"]["ending_scene"] == "别墅门外"


def test_reviewer_factual_context_json_is_complete_json(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_render_prompt(_template: str, **kwargs):
        captured["context_json"] = kwargs["context_json"]
        return "prompt"

    monkeypatch.setattr("app.services.generation.agents.render_prompt", _fake_render_prompt)
    monkeypatch.setattr("app.services.generation.agents.get_llm_with_fallback", lambda *_args, **_kwargs: _FakeLLM())
    monkeypatch.setattr(
        "app.services.generation.agents._invoke_json_with_schema",
        lambda *_args, **_kwargs: {
            "score": 0.8,
            "confidence": 0.8,
            "feedback": "ok",
            "positives": [],
            "must_fix": [],
            "should_fix": [],
            "risks": [],
            "contradictions": [],
        },
    )
    reviewer = ReviewerAgent()
    reviewer.run_factual_structured(
        draft="正文",
        chapter_num=2,
        context={
            "outline_contract": {"opening_scene": "主角卧室"},
            "thread_ledger": {"active_plotlines": ["调查真相"]},
            "previous_transition_state": {"ending_scene": "别墅门外"},
            "transition_constraints": {"opening_constraints": ["需要显式过渡"]},
            "anti_repeat_constraints": {"book_revealed_information": ["主角是云家嫡女"]},
            "story_bible_context": "角色状态: 林初(警觉)" * 100,
            "character_states": [{"name": "林初", "status": "警觉"}],
            "summaries": [{"chapter_num": 1, "summary": "前情回顾" * 100}],
        },
        provider="openai",
        model="gpt-4o-mini",
    )

    parsed = json.loads(str(captured["context_json"]))
    assert parsed["outline_contract"]["opening_scene"] == "主角卧室"
    assert parsed["previous_transition_state"]["ending_scene"] == "别墅门外"
