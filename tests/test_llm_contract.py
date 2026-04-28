from __future__ import annotations

import pytest

from app.core.llm import _TrackedLLMProxy
from app.core.llm_contract import get_last_prompt_meta, invoke_chapter_body_structured
from app.core.llm_usage import begin_usage_session, end_usage_session, snapshot_usage
from app.services.generation.contracts import ChapterBodySchema, OutputContractError


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


def test_invoke_contract_semantic_retry_appends_guidance(monkeypatch):
    prompts: list[str] = []

    class _SemanticRunner:
        def __init__(self):
            self.calls = 0

        def invoke(self, prompt: str):
            prompts.append(prompt)
            self.calls += 1
            if self.calls == 1:
                return {
                    "parsed": {"chapter_body": "太短"},
                    "raw": _RawResponse(),
                    "parsing_error": None,
                }
            return {
                "parsed": {"chapter_body": "第二次输出已经补足了有效剧情推进和章节内容。"},
                "raw": _RawResponse(),
                "parsing_error": None,
            }

    class _SemanticLLM:
        def __init__(self):
            self.runner = _SemanticRunner()

        def with_structured_output(self, _schema, *, method: str, include_raw: bool, **_kwargs):
            assert method == "json_schema"
            assert include_raw is True
            return self.runner

    fake = _SemanticLLM()
    monkeypatch.setattr("app.core.llm_contract.get_llm", lambda *_args, **_kwargs: fake)

    out = invoke_chapter_body_structured(
        prompt="base prompt",
        stage="writer",
        provider="openai",
        model="m",
        chapter_num=2,
        retries=1,
        min_chars=10,
        prompt_template="next_chapter",
        prompt_version="v2",
    )

    assert out.startswith("第二次输出")
    assert len(prompts) == 2
    assert "业务语义校验未通过" in prompts[1]
    assert "chapter_body_too_short" in prompts[1]


def test_invoke_contract_schema_retry_appends_guidance(monkeypatch):
    prompts: list[str] = []

    class _SchemaRetryRunner:
        def __init__(self):
            self.calls = 0

        def invoke(self, prompt: str):
            prompts.append(prompt)
            self.calls += 1
            if self.calls == 1:
                ChapterBodySchema.model_validate({})
            return {
                "parsed": {"chapter_body": "第二次输出修复了 JSON schema 缺失字段问题。"},
                "raw": _RawResponse(),
                "parsing_error": None,
            }

    class _SchemaRetryLLM:
        def __init__(self):
            self.runner = _SchemaRetryRunner()

        def with_structured_output(self, _schema, *, method: str, include_raw: bool, **_kwargs):
            assert method == "json_schema"
            assert include_raw is True
            return self.runner

    fake = _SchemaRetryLLM()
    monkeypatch.setattr("app.core.llm_contract.get_llm", lambda *_args, **_kwargs: fake)

    out = invoke_chapter_body_structured(
        prompt="base prompt",
        stage="writer",
        provider="openai",
        model="m",
        chapter_num=2,
        retries=1,
        min_chars=10,
        prompt_template="next_chapter",
        prompt_version="v2",
    )

    assert out.startswith("第二次输出")
    assert len(prompts) == 2
    assert "业务语义校验未通过" in prompts[1]
    assert "chapter_body" in prompts[1]


class _FakeRaw:
    usage_metadata = {"input_tokens": 200, "output_tokens": 80, "total_tokens": 280}


class _FakeStructuredChain:
    def __init__(self, raw, parsed, parsing_error=None):
        self._result = {"raw": raw, "parsed": parsed, "parsing_error": parsing_error}

    def invoke(self, _prompt, **_kw):
        return self._result

    async def ainvoke(self, _prompt, **_kw):
        return self._result


class _FakeInnerLLM:
    def __init__(self, chain):
        self._chain = chain
        self.received_include_raw: bool | None = None

    def with_structured_output(self, _schema, *, include_raw: bool = False, **_kw):
        self.received_include_raw = include_raw
        return self._chain


def test_proxy_with_structured_output_records_tokens_include_raw_true():
    """Proxy override: caller passes include_raw=True, tokens auto-recorded, full dict returned."""
    raw = _FakeRaw()
    chain = _FakeStructuredChain(raw=raw, parsed={"chapter_body": "ok"})
    inner = _FakeInnerLLM(chain)
    proxy = _TrackedLLMProxy(inner, stage_prefix="test.writer")

    begin_usage_session("proxy-test-1")
    structured = proxy.with_structured_output(object, include_raw=True)
    assert inner.received_include_raw is True
    result = structured.invoke("prompt")
    assert result["parsed"] == {"chapter_body": "ok"}
    snap = snapshot_usage()
    assert snap["input_tokens"] == 200
    assert snap["output_tokens"] == 80
    assert snap["calls"] == 1
    end_usage_session()


def test_proxy_with_structured_output_records_tokens_include_raw_false():
    """Caller omits include_raw (default False): proxy forces True internally, records tokens,
    returns only the parsed object (not the full dict)."""
    raw = _FakeRaw()
    chain = _FakeStructuredChain(raw=raw, parsed={"chapter_body": "hello"})
    inner = _FakeInnerLLM(chain)
    proxy = _TrackedLLMProxy(inner, stage_prefix="test.reviewer")

    begin_usage_session("proxy-test-2")
    structured = proxy.with_structured_output(object)
    assert inner.received_include_raw is True
    result = structured.invoke("prompt")
    assert result == {"chapter_body": "hello"}   # parsed only, not full dict
    snap = snapshot_usage()
    assert snap["input_tokens"] == 200
    end_usage_session()


def test_proxy_with_structured_output_no_double_count():
    """One invoke = one token record. Two invokes = two records."""
    raw = _FakeRaw()
    chain = _FakeStructuredChain(raw=raw, parsed={"chapter_body": "x" * 300})
    inner = _FakeInnerLLM(chain)
    proxy = _TrackedLLMProxy(inner, stage_prefix="test.double")

    begin_usage_session("proxy-test-3")
    structured = proxy.with_structured_output(object, include_raw=True)
    structured.invoke("p1")
    structured.invoke("p2")
    snap = snapshot_usage()
    assert snap["input_tokens"] == 400   # 200 * 2
    assert snap["calls"] == 2
    end_usage_session()
