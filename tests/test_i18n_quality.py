import builtins

from app.core.i18n import evaluate_language_quality

GRAMMAR_SKIP_TEXT = "未启用语法检查（language_tool_python 不可用），跳过该项。"


def test_language_quality_report_hides_grammar_tool_unavailable(monkeypatch):
    original_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "language_tool_python":
            raise ImportError("missing in test")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    score, report = evaluate_language_quality("这是一个用于语言质量评估的长文本。" * 30, "zh")

    assert 0.0 <= score <= 1.0
    assert GRAMMAR_SKIP_TEXT not in report
