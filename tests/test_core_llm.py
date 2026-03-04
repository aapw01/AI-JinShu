from types import SimpleNamespace

from app.core import llm


def test_resolve_api_key_prefers_runtime_provider(monkeypatch):
    monkeypatch.setattr(llm, "get_provider_runtime", lambda provider: {"api_key": "db-key"})
    assert llm._resolve_api_key("openai") == "db-key"


def test_resolve_api_key_prefers_global_key_for_selected_provider(monkeypatch):
    monkeypatch.setattr(llm, "get_provider_runtime", lambda provider: None)
    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: SimpleNamespace(
            default_llm_provider="openai",
            llm_api_key="global-key",
            openai_api_key="oa",
            anthropic_api_key="an",
            gemini_api_key="gm",
        ),
    )
    assert llm._resolve_api_key("openai") == "global-key"
    assert llm._resolve_api_key("anthropic") == "an"


def test_resolve_base_url_applies_global_base_only_to_selected_provider(monkeypatch):
    monkeypatch.setattr(llm, "get_provider_runtime", lambda provider: None)
    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: SimpleNamespace(
            default_llm_provider="openai",
            llm_base_url="https://proxy.example.com/v1/",
            openai_base_url="https://oa.example.com/v1",
            anthropic_base_url="https://an.example.com/v1",
            gemini_base_url="https://gm.example.com/v1beta",
        ),
    )
    assert llm._resolve_base_url("anthropic") == "https://an.example.com"
    assert llm._resolve_base_url("openai") == "https://proxy.example.com/v1"


def test_get_llm_builds_from_resolved_provider_and_model(monkeypatch):
    monkeypatch.setattr(llm, "_resolve_chat_provider_and_model", lambda provider, model: ("my-provider", "m1"))
    monkeypatch.setattr(llm, "_build_chat_model", lambda provider, model: {"provider": provider, "model": model})
    got = llm.get_llm("unknown-provider", "m1")
    assert got["provider"] == "my-provider"
    assert got["model"] == "m1"


def test_get_llm_with_fallback_uses_order(monkeypatch):
    calls: list[tuple[str | None, str | None]] = []

    def _fake_get_llm(provider=None, model=None):
        calls.append((provider, model))
        if provider in (None, "bad", "openai"):
            raise RuntimeError("provider failed")
        if provider == "anthropic":
            return "ok-anthropic"
        raise RuntimeError("unexpected")

    monkeypatch.setattr(llm, "get_llm", _fake_get_llm)
    monkeypatch.setattr(llm, "get_enabled_provider_order", lambda: ["openai", "anthropic", "gemini"])
    got = llm.get_llm_with_fallback("bad", "model-x")
    assert got == "ok-anthropic"
    assert calls[0] == ("bad", "model-x")
    assert ("openai", None) in calls
    assert ("anthropic", None) in calls


def test_embed_query_empty_returns_none():
    assert llm.embed_query("") is None
    assert llm.embed_query("   ") is None


def test_embed_query_success(monkeypatch):
    monkeypatch.setattr(llm, "get_default_model_for_type", lambda model_type: {"provider_key": "openai", "model_name": "emb-x"})
    monkeypatch.setattr(llm, "get_settings", lambda: SimpleNamespace(default_embedding_model="emb-x"))

    class _M:
        def embed_query(self, text: str):
            return [0.1, 0.2, len(text)]

    monkeypatch.setattr(llm, "get_embedding_model", lambda: _M())
    out = llm.embed_query("hello")
    assert out == [0.1, 0.2, 5]


def test_embed_query_failure_returns_none(monkeypatch):
    monkeypatch.setattr(llm, "get_default_model_for_type", lambda model_type: {"provider_key": "openai", "model_name": "emb-x"})
    monkeypatch.setattr(llm, "get_settings", lambda: SimpleNamespace(default_embedding_model="emb-x"))

    class _M:
        def embed_query(self, text: str):
            raise RuntimeError("embed down")

    monkeypatch.setattr(llm, "get_embedding_model", lambda: _M())
    assert llm.embed_query("hello") is None
