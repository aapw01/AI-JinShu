from __future__ import annotations

import pytest

from app.core.llm_contract import get_last_prompt_meta, invoke_chapter_body_structured
from app.services.generation.contracts import OutputContractError


class _RawResponse:
    usage_metadata = {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}


class _Runner:
    def __init__(self, result):
        self._result = result

    def invoke(self, _prompt: str):
        return self._result


class _FakeLLM:
    def __init__(self, method_results: dict[str, object]):
        self.method_results = method_results
        self.calls: list[str] = []

    def with_structured_output(self, _schema, *, method: str, include_raw: bool, **_kwargs):
        assert include_raw is True
        self.calls.append(method)
        result = self.method_results.get(method)
        if isinstance(result, Exception):
            raise result
        return _Runner(result)


def test_invoke_contract_uses_method_fallback_for_openai(monkeypatch):
    fake = _FakeLLM(
        {
            "json_schema": {"parsed": None, "raw": _RawResponse(), "parsing_error": ValueError("bad json")},
            "function_calling": {
                "parsed": {"chapter_body": "这是一个足够长的正文段落，用于验证结构化调用器的方法回退。"},
                "raw": _RawResponse(),
                "parsing_error": None,
            },
        }
    )
    monkeypatch.setattr("app.core.llm_contract.get_llm", lambda *_args, **_kwargs: fake)
    out = invoke_chapter_body_structured(
        prompt="x",
        stage="writer",
        provider="openai",
        model="m",
        chapter_num=1,
        retries=0,
        min_chars=5,
        max_provider_fallbacks=0,
    )
    assert out.startswith("这是一个足够长的正文")
    assert fake.calls == ["json_schema", "function_calling"]
    meta = get_last_prompt_meta() or {}
    assert meta.get("stage") == "writer"
    assert meta.get("prompt_hash")


def test_invoke_contract_does_not_switch_provider(monkeypatch):
    fake = _FakeLLM(
        {
            "json_schema": {"parsed": None, "raw": _RawResponse(), "parsing_error": ValueError("bad json")},
            "function_calling": {"parsed": None, "raw": _RawResponse(), "parsing_error": ValueError("bad json")},
            "json_mode": {"parsed": None, "raw": _RawResponse(), "parsing_error": ValueError("bad json")},
        }
    )
    monkeypatch.setattr("app.core.llm_contract.get_llm", lambda *_args, **_kwargs: fake)
    with pytest.raises(OutputContractError) as exc_info:
        invoke_chapter_body_structured(
            prompt="x",
            stage="writer",
            provider="openai",
            model="m",
            chapter_num=1,
            retries=0,
            min_chars=5,
            max_provider_fallbacks=2,
        )
    assert exc_info.value.code == "MODEL_OUTPUT_CONTRACT_EXHAUSTED"
    assert fake.calls == ["json_schema", "function_calling", "json_mode"]


def test_invoke_contract_strict_exhausted_raises(monkeypatch):
    fake = _FakeLLM(
        {
            "json_schema": {"parsed": None, "raw": _RawResponse(), "parsing_error": ValueError("bad json")},
            "function_calling": {"parsed": None, "raw": _RawResponse(), "parsing_error": ValueError("bad json")},
            "json_mode": {"parsed": None, "raw": _RawResponse(), "parsing_error": ValueError("bad json")},
        }
    )
    monkeypatch.setattr("app.core.llm_contract.get_llm", lambda *_args, **_kwargs: fake)
    with pytest.raises(OutputContractError) as exc_info:
        invoke_chapter_body_structured(
            prompt="x",
            stage="writer",
            provider="openai",
            model="m",
            chapter_num=1,
            retries=0,
            min_chars=5,
            max_provider_fallbacks=0,
        )
    assert exc_info.value.code == "MODEL_OUTPUT_CONTRACT_EXHAUSTED"
