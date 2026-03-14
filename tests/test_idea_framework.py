import json
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
                '"selling_point":"高压节奏+反转身份+连续兑现。",'
                '"recommended_genre":"xuanhuan",'
                '"recommended_style":"web-power"}'
            )
        )


class _FailLLM:
    def invoke(self, prompt: str):
        raise RuntimeError("llm unavailable")


class _StructuredLLM:
    def invoke(self, prompt: str):
        from app.services.generation.idea_framework import IdeaFrameworkSchema

        return IdeaFrameworkSchema(
            one_liner="天启边城中，失忆剑士被迫在两大阵营间抉择命运。",
            premise="边城被天灾围困，各派争夺古代火种。",
            conflict="主角必须在救城与复仇之间二选一。",
            hook="开篇火种失窃，主角被诬陷为叛徒。",
            selling_point="高压节奏+反转身份+连续兑现。",
            recommended_genre="xuanhuan",
            recommended_style="web-power",
        )


class _DoubleEncodedLLM:
    def invoke(self, prompt: str):
        payload = json.dumps(
            {
                "one_liner": "天启边城中，失忆剑士被迫在两大阵营间抉择命运。",
                "premise": "边城被天灾围困，各派争夺古代火种。",
                "conflict": "主角必须在救城与复仇之间二选一。",
                "hook": "开篇火种失窃，主角被诬陷为叛徒。",
                "selling_point": "高压节奏+反转身份+连续兑现。",
                "recommended_genre": "xuanhuan",
                "recommended_style": "web-power",
            },
            ensure_ascii=False,
        )
        return SimpleNamespace(
            content=json.dumps(payload, ensure_ascii=False)
        )


def test_generate_idea_framework_success(monkeypatch):
    monkeypatch.setattr("app.services.generation.idea_framework.get_model_for_stage", lambda *_: ("openai", "gpt-4o-mini"))
    monkeypatch.setattr("app.services.generation.idea_framework.get_llm_with_fallback", lambda *_: _OkLLM())
    out = generate_idea_framework("天启边城", "zh", "xuanhuan", "web-power", "web-novel")
    assert out["one_liner"]
    assert out["premise"]
    assert out["conflict"]
    assert out["hook"]
    assert out["selling_point"]
    assert out["recommended_genre"] == "xuanhuan"
    assert out["recommended_style"] == "web-power"


def test_generate_idea_framework_fallback(monkeypatch):
    monkeypatch.setattr("app.services.generation.idea_framework.get_model_for_stage", lambda *_: ("openai", "gpt-4o-mini"))
    monkeypatch.setattr("app.services.generation.idea_framework.get_llm_with_fallback", lambda *_: _FailLLM())
    out = generate_idea_framework("断城", "zh", None, None, None)
    assert "断城" in out["one_liner"]
    assert out["conflict"]
    assert out.get("recommended_genre") is None
    assert out.get("recommended_style") is None


def test_generate_idea_framework_accepts_structured_output(monkeypatch):
    monkeypatch.setattr("app.services.generation.idea_framework.get_model_for_stage", lambda *_: ("openai", "gpt-4o-mini"))
    monkeypatch.setattr("app.services.generation.idea_framework.get_llm_with_fallback", lambda *_: _StructuredLLM())
    out = generate_idea_framework("天启边城", "zh", "xuanhuan", "web-power", "web-novel")
    assert out["hook"] == "开篇火种失窃，主角被诬陷为叛徒。"


def test_generate_idea_framework_accepts_double_encoded_json(monkeypatch):
    monkeypatch.setattr("app.services.generation.idea_framework.get_model_for_stage", lambda *_: ("openai", "gpt-4o-mini"))
    monkeypatch.setattr("app.services.generation.idea_framework.get_llm_with_fallback", lambda *_: _DoubleEncodedLLM())
    out = generate_idea_framework("天启边城", "zh", "xuanhuan", "web-power", "web-novel")
    assert out["selling_point"] == "高压节奏+反转身份+连续兑现。"


def test_generate_idea_framework_invalid_genre_id_sanitized(monkeypatch):
    """LLM returns an invalid genre ID → should be sanitized to None."""

    class _InvalidIdLLM:
        def invoke(self, prompt: str):
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "one_liner": "测试创意",
                        "premise": "测试背景",
                        "conflict": "测试冲突",
                        "hook": "测试钩子",
                        "selling_point": "测试卖点",
                        "recommended_genre": "invalid_genre_xyz",
                        "recommended_style": "invalid_style_xyz",
                    },
                    ensure_ascii=False,
                )
            )

    monkeypatch.setattr("app.services.generation.idea_framework.get_model_for_stage", lambda *_: ("openai", "gpt-4o-mini"))
    monkeypatch.setattr("app.services.generation.idea_framework.get_llm_with_fallback", lambda *_: _InvalidIdLLM())
    out = generate_idea_framework("测试小说", "zh", None, None, None)
    assert out["recommended_genre"] is None
    assert out["recommended_style"] is None


def test_generate_idea_framework_user_genre_preserved(monkeypatch):
    """When user already specified genre, it should be preserved regardless of LLM recommendation."""
    monkeypatch.setattr("app.services.generation.idea_framework.get_model_for_stage", lambda *_: ("openai", "gpt-4o-mini"))
    monkeypatch.setattr("app.services.generation.idea_framework.get_llm_with_fallback", lambda *_: _OkLLM())
    out = generate_idea_framework("天启边城", "zh", "dushi", "literary", "web-novel")
    assert out["recommended_genre"] == "dushi"
    assert out["recommended_style"] == "literary"


def test_detect_title_language():
    assert detect_title_language("全球降智：唯一的正常人") == "zh"
    assert detect_title_language("Fallen City") == "en"
    assert detect_title_language("テスト物語") == "ja"
