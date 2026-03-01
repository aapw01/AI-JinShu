import types
import sys

from app.core import i18n
from app.core import tokens


def test_estimate_tokens_uses_char_fallback_when_encoding_missing(monkeypatch):
    monkeypatch.setattr(tokens, "_ENCODING_READY", True)
    monkeypatch.setattr(tokens, "_ENCODING", None)
    assert tokens.estimate_tokens("abcd" * 8) == 8
    assert tokens.estimate_tokens("") == 1


def test_estimate_tokens_uses_encoding_when_available(monkeypatch):
    class _DummyEncoding:
        def encode(self, text: str):
            return list(range(len(text) // 2 + 1))

    monkeypatch.setattr(tokens, "_ENCODING_READY", True)
    monkeypatch.setattr(tokens, "_ENCODING", _DummyEncoding())
    assert tokens.estimate_tokens("abcdef") == 4


def test_get_native_style_profile_normalizes_alias():
    zh_profile = i18n.get_native_style_profile("zh-CN")
    assert "简洁有力" in zh_profile
    en_profile = i18n.get_native_style_profile("xx-YY")
    assert "Natural native prose" in en_profile


def test_evaluate_language_quality_short_text_returns_low_score():
    score, report = i18n.evaluate_language_quality("太短了", "zh")
    assert score == 0.2
    assert "文本过短" in report


def test_evaluate_language_quality_bounds(monkeypatch):
    dummy_langdetect = types.SimpleNamespace(
        detect_langs=lambda _text: [types.SimpleNamespace(lang="zh", prob=0.99)]
    )
    dummy_language_tool = types.SimpleNamespace(
        LanguageTool=lambda _lang: (_ for _ in ()).throw(RuntimeError("no local tool")),
        LanguageToolPublicAPI=lambda _lang: types.SimpleNamespace(check=lambda _t: []),
    )
    monkeypatch.setitem(sys.modules, "langdetect", dummy_langdetect)
    monkeypatch.setitem(sys.modules, "language_tool_python", dummy_language_tool)
    text = "这是一个用于质量检测的中文段落。" * 30
    score, report = i18n.evaluate_language_quality(text, "zh-CN")
    assert 0.0 <= score <= 1.0
    assert isinstance(report, str) and report
