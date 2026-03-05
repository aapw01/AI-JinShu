from app.api.routes.chapters import _resolve_progress_title
from app.services.generation.common import (
    detect_chapter_content_contamination,
    extract_chapter_body_from_response,
    is_effective_title,
    normalize_title_text,
    resolve_chapter_title,
    sanitize_chapter_content_for_storage,
)


def test_normalize_title_text_collapses_repeated_prefix() -> None:
    assert normalize_title_text("第12章 第12章 逆风翻盘") == "第12章 逆风翻盘"


def test_is_effective_title_filters_placeholders() -> None:
    assert is_effective_title("第19章", 19) is False
    assert is_effective_title("Chapter 19", 19) is False
    assert is_effective_title("第19章 幸存者的坐标", 19) is True
    assert is_effective_title("## 第19章", 19) is False
    assert is_effective_title("**第19章**", 19) is False
    assert is_effective_title("****", 19) is False
    assert is_effective_title("第19章：根据反馈全面打磨后的修订版", 19) is False


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


def test_sanitize_chapter_content_removes_preface_and_markdown_heading() -> None:
    raw = """以下是根据反馈全面打磨后的第9章修订版。
在**保留原有高密度意象**的前提下，重点解决如下问题：
- **校准时间/身份/道具闭环**：补齐序列逻辑
---
## 第9章 你的号码是13

热电厂B4控制室。
林晚抬头。"""
    cleaned = sanitize_chapter_content_for_storage(raw, chapter_num=9)
    assert cleaned.startswith("热电厂B4控制室。")
    assert "以下是根据反馈" not in cleaned
    assert "## 第9章" not in cleaned


def test_sanitize_chapter_content_keeps_normal_bullet_opening() -> None:
    raw = "- 门被推开。\n他看向窗外。"
    cleaned = sanitize_chapter_content_for_storage(raw, chapter_num=3)
    assert cleaned == raw


def test_extract_chapter_body_from_tagged_response() -> None:
    raw = "说明文本应被忽略\n<chapter_body>\n第一句。\n第二句。\n</chapter_body>\n尾部注释"
    body = extract_chapter_body_from_response(raw)
    assert body == "第一句。\n第二句。"


def test_sanitize_does_not_strip_normal_opening_sentence() -> None:
    raw = "要求你立刻撤离，林晚却把门反锁了。\n她盯着屏幕上的倒计时。"
    cleaned = sanitize_chapter_content_for_storage(raw, chapter_num=6)
    assert cleaned == raw


def test_detect_chapter_content_contamination_true_on_preface() -> None:
    raw = "以下是根据反馈全面打磨后的第9章修订版。\n重点解决如下问题：\n- **补齐逻辑**\n---\n正文第一句。"
    report = detect_chapter_content_contamination(raw, chapter_num=9)
    assert report["contaminated"] is True
    assert "strong_preface_marker" in report["reasons"]


def test_detect_chapter_content_contamination_false_on_clean_content() -> None:
    raw = "热电厂B4控制室里，灯管闪了两下。\n林晚把指尖按在回车键上。"
    report = detect_chapter_content_contamination(raw, chapter_num=9)
    assert report["contaminated"] is False
