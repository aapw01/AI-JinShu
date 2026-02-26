from types import SimpleNamespace

from app.services.generation.idea_framework import detect_title_language, generate_idea_framework


class _OkLLM:
    def invoke(self, prompt: str):
        return SimpleNamespace(
            content=(
                '{"one_liner":"天启边城中，失忆剑士被迫在两大阵营间抉择命运。",'
                '"premise":"边城被天灾围困，各派争夺古代火种。",'
                '"conflict":"主角必须在救城与复仇之间二选一。",'
                '"hook":"开篇火种失窃，主角被诬陷为叛徒。",'
                '"selling_point":"高压节奏+反转身份+连续兑现。"}'
            )
        )


class _FailLLM:
    def invoke(self, prompt: str):
        raise RuntimeError("llm unavailable")


def test_generate_idea_framework_success(monkeypatch):
    monkeypatch.setattr("app.services.generation.idea_framework.get_model_for_stage", lambda *_: ("openai", "gpt-4o-mini"))
    monkeypatch.setattr("app.services.generation.idea_framework.get_llm_with_fallback", lambda *_: _OkLLM())
    out = generate_idea_framework("天启边城", "zh", "xuanhuan", "web-power", "web-novel")
    assert out["one_liner"]
    assert out["premise"]
    assert out["conflict"]
    assert out["hook"]
    assert out["selling_point"]


def test_generate_idea_framework_fallback(monkeypatch):
    monkeypatch.setattr("app.services.generation.idea_framework.get_model_for_stage", lambda *_: ("openai", "gpt-4o-mini"))
    monkeypatch.setattr("app.services.generation.idea_framework.get_llm_with_fallback", lambda *_: _FailLLM())
    out = generate_idea_framework("断城", "zh", None, None, None)
    assert "断城" in out["one_liner"]
    assert out["conflict"]


def test_detect_title_language():
    assert detect_title_language("全球降智：唯一的正常人") == "zh"
    assert detect_title_language("Fallen City") == "en"
    assert detect_title_language("テスト物語") == "ja"
