from __future__ import annotations

import pytest

from app.services.generation.agents import FactExtractorAgent, ReviewerAgent
from app.services.generation.contracts import OutputContractError


class _Resp:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def invoke(self, _prompt: str):
        return _Resp("not-a-json-payload")


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
