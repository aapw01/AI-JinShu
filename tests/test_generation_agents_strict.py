from __future__ import annotations

import json

import pytest

from app.services.generation.agents import (
    FactExtractorAgent,
    FinalReviewerAgent,
    FinalizerAgent,
    PrewritePlannerAgent,
    ProgressionMemoryAgent,
    ReviewerAgent,
    WriterAgent,
)
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


def test_fact_extractor_trims_large_lists(monkeypatch):
    monkeypatch.setattr("app.services.generation.agents.get_llm_with_fallback", lambda *_args, **_kwargs: _FakeLLM())
    monkeypatch.setattr(
        "app.services.generation.agents._invoke_json_with_schema",
        lambda *_args, **_kwargs: {
            "events": [{"id": str(i)} for i in range(20)],
            "entities": [{"name": f"角色{i}"} for i in range(18)],
            "facts": [{"entity_name": f"角色{i}"} for i in range(24)],
        },
    )
    agent = FactExtractorAgent()
    result = agent.run(
        chapter_num=1,
        content="正文",
        outline={"title": "t"},
        language="zh",
        provider="openai",
        model="test-model",
    )
    assert len(result["events"]) <= 12
    assert len(result["entities"]) <= 12
    assert len(result["facts"]) <= 16


def test_prewrite_planner_invalid_specification_falls_back_to_typed_defaults(monkeypatch):
    monkeypatch.setattr("app.services.generation.agents.get_llm_with_fallback", lambda *_args, **_kwargs: _FakeLLM())
    agent = PrewritePlannerAgent()
    result = agent.run(
        novel={"title": "测试小说", "user_idea": "真假千金复仇"},
        num_chapters=20,
        language="zh",
        provider="openai",
        model="test-model",
    )
    specification = result["specification"]
    assert isinstance(specification, dict)
    assert isinstance(specification.get("characters"), list)
    assert specification["characters"]
    assert specification["characters"][0]["name"]


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


def test_writer_agent_passes_inference_to_contract(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr("app.services.generation.agents.render_prompt", lambda *_args, **_kwargs: "prompt")

    def _fake_invoke(**kwargs):
        captured["inference"] = kwargs.get("inference")
        return "正文"

    monkeypatch.setattr("app.services.generation.agents.invoke_chapter_body_structured", _fake_invoke)

    out = WriterAgent().run(
        novel_id="n-1",
        chapter_num=3,
        outline={"title": "第3章"},
        context={},
        provider="openai",
        model="gpt-4o-mini",
        inference={"temperature": 0.12},
    )

    assert out == "正文"
    assert captured["inference"] == {"temperature": 0.12}


def test_finalizer_agent_passes_inference_to_contract(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr("app.services.generation.agents.render_prompt", lambda *_args, **_kwargs: "prompt")

    def _fake_invoke(**kwargs):
        captured["inference"] = kwargs.get("inference")
        return "定稿"

    monkeypatch.setattr("app.services.generation.agents.invoke_chapter_body_structured", _fake_invoke)

    out = FinalizerAgent().run(
        draft="草稿",
        feedback="收紧重复",
        provider="openai",
        model="gpt-4o-mini",
        inference={"temperature": 0.08},
    )

    assert out == "定稿"
    assert captured["inference"] == {"temperature": 0.08}


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


def test_final_reviewer_parse_failure_returns_warning_fallback(monkeypatch):
    monkeypatch.setattr("app.services.generation.agents.get_llm_with_fallback", lambda *_args, **_kwargs: _FakeLLM())
    reviewer = FinalReviewerAgent()
    result = reviewer.run_full_book(
        volume_reports=[],
        recent_summaries=[{"chapter_num": i, "summary": f"第{i}章摘要"} for i in range(1, 6)],
        unresolved_foreshadows=[],
        provider="openai",
        model="test-model",
    )
    assert result["fallback"] is True
    assert result["score"] < 0.7
    assert result["must_fix"]


def test_final_reviewer_run_full_book_passes_new_kwargs_to_render_prompt(monkeypatch):
    """run_full_book passes volume_reports/recent_summaries/unresolved_foreshadows to render_prompt."""
    captured: dict[str, object] = {}

    def _fake_render_prompt(_template: str, **kwargs):
        captured.update(kwargs)
        return "prompt"

    monkeypatch.setattr("app.services.generation.agents.render_prompt", _fake_render_prompt)
    monkeypatch.setattr("app.services.generation.agents.get_llm_with_fallback", lambda *_args, **_kwargs: _FakeLLM())
    monkeypatch.setattr(
        "app.services.generation.agents._invoke_json_with_schema",
        lambda *_args, **_kwargs: {
            "score": 0.82,
            "confidence": 0.78,
            "feedback": "ok",
            "must_fix": [],
            "should_improve": [],
            "fallback": False,
        },
    )
    reviewer = FinalReviewerAgent()
    vr = [{"volume_no": "1", "verdict": "pass", "metrics": {}}]
    rs = [{"chapter_num": 30, "summary": "结局"}]
    uf = [{"title": "钥匙", "planted_chapter": 3}]
    reviewer.run_full_book(
        volume_reports=vr,
        recent_summaries=rs,
        unresolved_foreshadows=uf,
        provider="openai",
        model="test-model",
    )
    assert captured.get("volume_reports") == vr
    assert captured.get("recent_summaries") == rs
    assert captured.get("unresolved_foreshadows") == uf


def test_progression_memory_agent_trims_large_lists(monkeypatch):
    monkeypatch.setattr("app.services.generation.agents.get_llm_with_fallback", lambda *_args, **_kwargs: _FakeLLM())
    monkeypatch.setattr(
        "app.services.generation.agents._invoke_json_with_schema",
        lambda *_args, **_kwargs: {
            "advancement": {
                "actual_progress": "推进主线",
                "new_information": [f"信息{i}" for i in range(10)],
                "resolved_threads": [f"已解{i}" for i in range(9)],
                "new_unresolved_threads": [f"未解{i}" for i in range(8)],
                "forbidden_repeats": [f"禁复{i}" for i in range(7)],
                "major_beats": [f"桥段{i}" for i in range(11)],
            },
            "transition": {
                "ending_scene": "医院门口",
                "character_positions": [f"角色{i}@位置" for i in range(9)],
                "last_action": "转身离开",
            },
            "advancement_confidence": 0.81,
            "transition_confidence": 0.84,
            "validation_notes": [f"说明{i}" for i in range(12)],
        },
    )
    agent = ProgressionMemoryAgent()
    result = agent.run(
        chapter_num=3,
        content="正文",
        outline={"title": "test"},
        provider="openai",
        model="test-model",
    )
    assert len(result["advancement"]["new_information"]) == 5
    assert len(result["advancement"]["resolved_threads"]) == 5
    assert len(result["advancement"]["new_unresolved_threads"]) == 5
    assert len(result["advancement"]["forbidden_repeats"]) == 5
    assert len(result["advancement"]["major_beats"]) == 5
    assert len(result["transition"]["character_positions"]) == 5
    assert len(result["validation_notes"]) == 6


def test_progression_memory_agent_prompt_includes_constitution_and_memory_policy(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr("app.services.generation.agents.get_llm_with_fallback", lambda *_args, **_kwargs: _FakeLLM())

    def _capture(_llm, prompt, *_args, **_kwargs):
        captured["prompt"] = prompt
        return {
            "advancement": {},
            "transition": {},
            "advancement_confidence": 0.2,
            "transition_confidence": 0.2,
            "validation_notes": [],
        }

    monkeypatch.setattr("app.services.generation.agents._invoke_json_with_schema", _capture)

    ProgressionMemoryAgent().run(
        chapter_num=4,
        content="主角在旧宅中找到父亲遗留的账册，并决定主动入局。",
        outline={"title": "旧宅夜探", "chapter_objective": "找到证据"},
        provider="openai",
        model="test-model",
    )

    prompt = str(captured["prompt"])
    assert "你是推进记忆与衔接记忆抽取角色" in prompt
    assert "只有正文中明确发生的事实才能进入后续强约束" in prompt


def test_reviewer_factual_prompt_includes_constitution_and_memory_policy(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr("app.services.generation.agents.get_llm_with_fallback", lambda *_args, **_kwargs: _FakeLLM())

    def _capture(_llm, prompt, *_args, **_kwargs):
        captured["prompt"] = prompt
        return {
            "score": 0.8,
            "confidence": 0.7,
            "feedback": "ok",
            "positives": [],
            "must_fix": [],
            "should_fix": [],
            "risks": [],
            "contradictions": [],
        }

    monkeypatch.setattr("app.services.generation.agents._invoke_json_with_schema", _capture)

    ReviewerAgent().run_factual_structured(
        draft="主角从医院瞬移回到旧宅。",
        chapter_num=5,
        context={
            "previous_transition_state": {"ending_scene": "医院"},
            "outline_contract": {"opening_scene": "旧宅"},
        },
        provider="openai",
        model="test-model",
    )

    prompt = str(captured["prompt"])
    assert "你是事实一致性审校角色" in prompt
    assert "如果记忆与当前正文证据冲突，以当前正文和已落库章节事实为准" in prompt


def test_finalizer_prompt_includes_constitution_and_style_overlay(monkeypatch):
    captured: dict[str, object] = {}

    def _capture(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        return "定稿正文"

    monkeypatch.setattr("app.services.generation.agents.invoke_chapter_body_structured", _capture)

    FinalizerAgent().run(
        draft="草稿正文",
        feedback="修补衔接并收紧重复",
        language="zh",
        provider="openai",
        model="test-model",
    )

    prompt = str(captured["prompt"])
    assert "你是定稿编辑角色" in prompt
    assert "保持网文的推进效率、钩子意识和信息密度" in prompt
