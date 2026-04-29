import pytest

from app.core import llm


def test_resolve_api_key_uses_primary_runtime(monkeypatch):
    monkeypatch.setattr(llm, "get_primary_chat_runtime", lambda: {"api_key": "db-key"})
    assert llm._resolve_api_key("openai") == "db-key"
    assert llm._resolve_api_key("gemini") == "db-key"


def test_resolve_base_url_uses_primary_runtime(monkeypatch):
    monkeypatch.setattr(
        llm,
        "get_primary_chat_runtime",
        lambda: {"base_url": "https://proxy.example.com/v1/"},
    )
    assert llm._resolve_base_url("openai") == "https://proxy.example.com/v1"


def test_adapter_override_wins(monkeypatch):
    monkeypatch.setattr(
        llm,
        "get_primary_chat_runtime",
        lambda: {
            "provider": "gemini",
            "base_url": "http://gateway.example.com/v1",
            "protocol_override": "gemini",
        },
    )
    adapter, source = llm.resolve_effective_adapter("gemini")
    assert adapter == "gemini"
    assert source == "override"


def test_adapter_custom_base_url_defaults_to_openai_compatible(monkeypatch):
    monkeypatch.setattr(
        llm,
        "get_primary_chat_runtime",
        lambda: {
            "provider": "gemini",
            "base_url": "http://gateway.example.com/v1",
            "protocol_override": None,
        },
    )
    adapter, source = llm.resolve_effective_adapter("gemini")
    assert adapter == "openai_compatible"
    assert source == "auto_infer"


def test_adapter_without_base_url_uses_provider_native(monkeypatch):
    monkeypatch.setattr(
        llm,
        "get_primary_chat_runtime",
        lambda: {
            "provider": "gemini",
            "base_url": None,
            "protocol_override": None,
        },
    )
    adapter, source = llm.resolve_effective_adapter("gemini")
    assert adapter == "gemini"
    assert source == "auto_native"


def test_get_llm_ignores_non_primary_provider_override(monkeypatch):
    monkeypatch.setattr(
        llm,
        "get_primary_chat_runtime",
        lambda: {"provider": "gemini", "model": "gemini-2.5-pro"},
    )
    monkeypatch.setattr(
        llm,
        "_build_chat_model",
        lambda provider, model, inference=None: {
            "provider": provider,
            "model": model,
            "inference": inference,
        },
    )
    got = llm.get_llm("openai", "custom-model")
    assert got["provider"] == "gemini"
    assert got["model"] == "custom-model"


def test_build_chat_model_gemini_uses_custom_base_url(monkeypatch):
    captured: dict[str, str] = {}

    class _FakeGemini:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm, "ChatGoogleGenerativeAI", _FakeGemini)
    monkeypatch.setattr(
        llm,
        "get_primary_chat_runtime",
        lambda: {"provider": "gemini", "base_url": "http://gateway.example.com/v1beta"},
    )
    monkeypatch.setattr(
        llm, "resolve_effective_adapter", lambda provider: ("gemini", "override")
    )
    monkeypatch.setattr(llm, "_resolve_api_key", lambda provider: "sk-gm")
    monkeypatch.setattr(
        llm, "_resolve_base_url", lambda provider: "http://gateway.example.com/v1beta"
    )
    model = llm._build_chat_model("gemini", "gemini-3-flash-preview")
    assert isinstance(model, _FakeGemini)
    assert captured["model"] == "gemini-3-flash-preview"
    assert captured["google_api_key"] == "sk-gm"
    assert captured["base_url"] == "http://gateway.example.com/v1beta"


def test_build_chat_model_gemini_applies_temperature_and_safety(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeGemini:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm, "ChatGoogleGenerativeAI", _FakeGemini)
    monkeypatch.setattr(llm, "get_primary_chat_runtime", lambda: {"provider": "gemini"})
    monkeypatch.setattr(
        llm, "resolve_effective_adapter", lambda provider: ("gemini", "override")
    )
    monkeypatch.setattr(llm, "_resolve_api_key", lambda provider: "sk-gm")
    monkeypatch.setattr(llm, "_resolve_base_url", lambda provider: None)
    llm._build_chat_model(
        "gemini",
        "gemini-3-flash-preview",
        inference={
            "temperature": 0.1,
            "gemini": {
                "safety_settings": [
                    {
                        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                        "threshold": "BLOCK_ONLY_HIGH",
                    }
                ]
            },
        },
    )
    assert captured["temperature"] == 0.1
    safety_settings = captured["safety_settings"]
    assert len(safety_settings) == 1
    [(category, threshold)] = list(safety_settings.items())
    assert category.value == "HARM_CATEGORY_DANGEROUS_CONTENT"
    assert threshold.value == "BLOCK_ONLY_HIGH"


def test_build_chat_model_anthropic_disables_thinking(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeAnthropic:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm, "ChatAnthropic", _FakeAnthropic)
    monkeypatch.setattr(
        llm, "get_primary_chat_runtime", lambda: {"provider": "anthropic"}
    )
    monkeypatch.setattr(
        llm, "resolve_effective_adapter", lambda provider: ("anthropic", "override")
    )
    monkeypatch.setattr(llm, "_resolve_api_key", lambda provider: "sk-ant")
    monkeypatch.setattr(llm, "_resolve_base_url", lambda provider: None)

    llm._build_chat_model("anthropic", "claude-sonnet-4-5")

    assert captured["thinking"] == {"type": "disabled"}


def test_build_chat_model_gemini_disables_thinking(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeGemini:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm, "ChatGoogleGenerativeAI", _FakeGemini)
    monkeypatch.setattr(llm, "get_primary_chat_runtime", lambda: {"provider": "gemini"})
    monkeypatch.setattr(
        llm, "resolve_effective_adapter", lambda provider: ("gemini", "override")
    )
    monkeypatch.setattr(llm, "_resolve_api_key", lambda provider: "sk-gm")
    monkeypatch.setattr(llm, "_resolve_base_url", lambda provider: None)

    llm._build_chat_model("gemini", "gemini-2.5-pro")

    assert captured["thinking_budget"] == 0
    assert captured["include_thoughts"] is False


def test_build_chat_model_openai_reasoning_models_use_minimal_reasoning(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm, "ChatOpenAI", _FakeOpenAI)
    monkeypatch.setattr(llm, "get_primary_chat_runtime", lambda: {"provider": "openai"})
    monkeypatch.setattr(
        llm,
        "resolve_effective_adapter",
        lambda provider: ("openai_compatible", "override"),
    )
    monkeypatch.setattr(llm, "_resolve_api_key", lambda provider: "sk-openai")
    monkeypatch.setattr(llm, "_resolve_base_url", lambda provider: None)

    llm._build_chat_model("openai", "o3-mini", inference={"reasoning_effort": "high"})

    assert captured["reasoning_effort"] == "minimal"
    assert "reasoning" not in captured


def test_build_chat_model_custom_openai_gateway_disables_thinking(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm, "ChatOpenAI", _FakeOpenAI)
    monkeypatch.setattr(
        llm,
        "get_primary_chat_runtime",
        lambda: {
            "provider": "openai",
            "model": "qwen3-coder",
            "base_url": "http://gateway.example.com/v1",
        },
    )
    monkeypatch.setattr(
        llm,
        "resolve_effective_adapter",
        lambda provider: ("openai_compatible", "auto_infer"),
    )
    monkeypatch.setattr(llm, "_resolve_api_key", lambda provider: "sk-gateway")
    monkeypatch.setattr(
        llm, "_resolve_base_url", lambda provider: "http://gateway.example.com/v1"
    )

    llm._build_chat_model("openai", "qwen3-coder")

    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}


def test_build_chat_model_anthropic_openai_gateway_disables_thinking(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm, "ChatOpenAI", _FakeOpenAI)
    monkeypatch.setattr(
        llm,
        "get_primary_chat_runtime",
        lambda: {
            "provider": "anthropic",
            "base_url": "http://gateway.example.com/v1",
        },
    )
    monkeypatch.setattr(
        llm,
        "resolve_effective_adapter",
        lambda provider: ("openai_compatible", "auto_infer"),
    )
    monkeypatch.setattr(llm, "_resolve_api_key", lambda provider: "sk-gateway")
    monkeypatch.setattr(
        llm, "_resolve_base_url", lambda provider: "http://gateway.example.com/v1"
    )

    llm._build_chat_model("anthropic", "claude-sonnet-4-5")

    assert captured["extra_body"] == {"thinking": {"type": "disabled"}}


def test_build_chat_model_gemini_openai_gateway_disables_thinking(monkeypatch):
    captured: dict[str, object] = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm, "ChatOpenAI", _FakeOpenAI)
    monkeypatch.setattr(
        llm,
        "get_primary_chat_runtime",
        lambda: {
            "provider": "gemini",
            "base_url": "http://gateway.example.com/v1",
        },
    )
    monkeypatch.setattr(
        llm,
        "resolve_effective_adapter",
        lambda provider: ("openai_compatible", "auto_infer"),
    )
    monkeypatch.setattr(llm, "_resolve_api_key", lambda provider: "sk-gateway")
    monkeypatch.setattr(
        llm, "_resolve_base_url", lambda provider: "http://gateway.example.com/v1"
    )

    llm._build_chat_model("gemini", "gemini-2.5-pro")

    assert captured["extra_body"] == {
        "thinking_config": {"thinking_budget": 0, "include_thoughts": False}
    }


def test_litellm_fallback_disables_native_thinking(monkeypatch):
    from app.services.generation import idea_framework

    captured: dict[str, object] = {}

    class _Message:
        content = "{}"

    class _Choice:
        message = _Message()

    class _Resp:
        choices = [_Choice()]

    def _completion(**kwargs):
        captured.update(kwargs)
        return _Resp()

    monkeypatch.setattr(idea_framework.litellm, "completion", _completion)
    monkeypatch.setattr(
        idea_framework,
        "get_primary_chat_runtime",
        lambda: {"provider": "anthropic", "api_key": "sk-ant", "base_url": None},
    )
    monkeypatch.setattr(
        idea_framework,
        "resolve_effective_adapter",
        lambda provider: ("anthropic", "override"),
    )

    idea_framework._litellm_completion("claude-sonnet-4-5", "prompt")

    assert captured["thinking"] == {"type": "disabled"}


def test_normalize_inference_ignores_gemini_settings_on_non_gemini(monkeypatch):
    monkeypatch.setattr(
        llm,
        "resolve_effective_adapter",
        lambda provider: ("openai_compatible", "override"),
    )
    normalized = llm.normalize_inference_for_provider(
        "openai",
        {
            "temperature": 0.2,
            "gemini": {
                "safety_settings": [
                    {
                        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                        "threshold": "BLOCK_ONLY_HIGH",
                    }
                ]
            },
        },
    )
    assert normalized == {"temperature": 0.2}


def test_extract_provider_block_reads_gemini_prompt_feedback():
    class _Resp:
        response_metadata = {
            "promptFeedback": {
                "blockReason": "PROHIBITED_CONTENT",
                "blockReasonMessage": "blocked by policy",
            }
        }

    block = llm.extract_provider_block(_Resp())
    assert block == {
        "reason": "PROHIBITED_CONTENT",
        "message": "blocked by policy",
    }


def test_get_llm_with_fallback_is_single_model_wrapper(monkeypatch):
    monkeypatch.setattr(
        llm,
        "get_llm",
        lambda provider=None, model=None, inference=None: {
            "provider": provider,
            "model": model,
            "inference": inference,
        },
    )
    assert llm.get_llm_with_fallback("openai", "m1") == {
        "provider": "openai",
        "model": "m1",
        "inference": None,
    }


def test_get_embedding_model_reuses_primary_openai_compatible(monkeypatch):
    captured: dict[str, str] = {}

    class _FakeEmbeddings:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm, "OpenAIEmbeddings", _FakeEmbeddings)
    monkeypatch.setattr(
        llm,
        "get_embedding_runtime",
        lambda: {
            "enabled": True,
            "model": "text-embedding-3-small",
            "reuse_primary_connection": True,
        },
    )
    monkeypatch.setattr(
        llm,
        "get_primary_chat_runtime",
        lambda: {
            "resolved_protocol": "openai_compatible",
            "api_key": "sk-primary",
            "base_url": "http://gateway.example.com/v1",
        },
    )
    model = llm.get_embedding_model()
    assert isinstance(model, _FakeEmbeddings)
    assert captured["model"] == "text-embedding-3-small"
    assert captured["api_key"] == "sk-primary"
    assert captured["base_url"] == "http://gateway.example.com/v1"


def test_get_embedding_model_rejects_native_primary_reuse(monkeypatch):
    monkeypatch.setattr(
        llm,
        "get_embedding_runtime",
        lambda: {
            "enabled": True,
            "model": "text-embedding-3-small",
            "reuse_primary_connection": True,
        },
    )
    monkeypatch.setattr(
        llm, "get_primary_chat_runtime", lambda: {"resolved_protocol": "gemini"}
    )
    with pytest.raises(RuntimeError, match="non-OpenAI-compatible primary connection"):
        llm.get_embedding_model()


def test_get_embedding_model_uses_dedicated_connection(monkeypatch):
    captured: dict[str, str] = {}

    class _FakeEmbeddings:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm, "OpenAIEmbeddings", _FakeEmbeddings)
    monkeypatch.setattr(
        llm,
        "get_embedding_runtime",
        lambda: {
            "enabled": True,
            "model": "emb-x",
            "reuse_primary_connection": False,
            "resolved_protocol": "openai_compatible",
            "api_key": "sk-embed",
            "base_url": "http://embed.example.com/v1",
        },
    )
    model = llm.get_embedding_model()
    assert isinstance(model, _FakeEmbeddings)
    assert captured["model"] == "emb-x"
    assert captured["api_key"] == "sk-embed"
    assert captured["base_url"] == "http://embed.example.com/v1"


def test_embed_query_empty_returns_none():
    assert llm.embed_query("") is None
    assert llm.embed_query("   ") is None


def test_embed_query_success(monkeypatch):
    class _M:
        def embed_query(self, text: str):
            return [0.1, 0.2, len(text)]

    monkeypatch.setattr(
        llm,
        "get_embedding_runtime",
        lambda: {"enabled": True, "model": "emb-x", "reuse_primary_connection": False},
    )
    monkeypatch.setattr(llm, "get_embedding_model", lambda: _M())
    out = llm.embed_query("hello")
    assert out == [0.1, 0.2, 5]


def test_embed_query_failure_returns_none(monkeypatch):
    class _M:
        def embed_query(self, text: str):
            raise RuntimeError("embed down")

    monkeypatch.setattr(
        llm,
        "get_embedding_runtime",
        lambda: {"enabled": True, "model": "emb-x", "reuse_primary_connection": False},
    )
    monkeypatch.setattr(llm, "get_embedding_model", lambda: _M())
    assert llm.embed_query("hello") is None
