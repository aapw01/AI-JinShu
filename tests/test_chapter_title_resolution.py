from app.api.routes.chapters import _resolve_progress_title
from app.services.generation.common import (
    is_effective_title,
    normalize_title_text,
    resolve_chapter_title,
)


def test_normalize_title_text_collapses_repeated_prefix() -> None:
    assert normalize_title_text("第12章 第12章 逆风翻盘") == "第12章 逆风翻盘"


def test_is_effective_title_filters_placeholders() -> None:
    assert is_effective_title("第19章", 19) is False
    assert is_effective_title("Chapter 19", 19) is False
    assert is_effective_title("第19章 幸存者的坐标", 19) is True


def test_resolve_chapter_title_keeps_valid_title() -> None:
    assert resolve_chapter_title(8, title="雪夜追凶") == "雪夜追凶"


def test_resolve_chapter_title_generates_from_outline_fields() -> None:
    title = resolve_chapter_title(
        15,
        title="",
        outline={"summary": "主角在废墟中找到第一块钥匙碎片，并决定潜入高塔"},
    )
    assert title.startswith("第15章：")
    assert "关键事件" not in title


def test_resolve_chapter_title_uses_safe_fallback() -> None:
    assert resolve_chapter_title(21, title="  ", outline={}, summary="", content="") == "第21章：关键事件"


def test_progress_title_prefers_generated_then_outline_then_fallback() -> None:
    assert _resolve_progress_title(10, "第10章", "黑雨将至") == "黑雨将至"
    assert _resolve_progress_title(10, "遗失的名单", "") == "遗失的名单"
    assert _resolve_progress_title(10, "", "") == "第10章：关键事件"
