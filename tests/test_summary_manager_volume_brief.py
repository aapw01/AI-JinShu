"""Tests for SummaryManager.get_volume_brief key-sentence extraction.

旧实现是 join 后 chars_per_volume 简单截断，关键事件经常被前 400 字切掉。
这里在不依赖 LLM 的前提下用纯规则验证：
1. 关键事件句（决定 / 反转 / 重伤 / 死亡 等）即使位于章节末尾也会被保留下来。
2. 每章首句作为锚点必定保留（保证起始上下文可读）。
3. chars_per_volume 上限被严格遵守。
"""
from __future__ import annotations

from app.services.memory.summary_manager import (
    SummaryManager,
    _build_volume_brief_text,
    _is_key_sentence,
)


def _make_chapter_summary(chapter_num: int, summary: str):
    """Construct a minimal ChapterSummary-like object for the volume_brief test path."""
    class _Row:
        pass

    row = _Row()
    row.chapter_num = chapter_num
    row.summary = summary
    return row


# ---------------------------------------------------------------------------
# Helper-level coverage
# ---------------------------------------------------------------------------

def test_is_key_sentence_detects_event_keywords():
    assert _is_key_sentence("主角决定退出江湖。")
    assert _is_key_sentence("林霜在街头中伏，重伤昏迷。")
    assert not _is_key_sentence("天气晴好，街上行人稀少。")


def test_build_volume_brief_keeps_anchor_and_key_even_when_buried_at_end():
    items = [
        (
            1,
            "天色微亮。" "街上行人稀少。" "市集开张。" "主角决定离开京城。",
        ),
        (
            2,
            "晨雾散去。" "客栈热闹。" "林霜在街头中伏，重伤昏迷。",
        ),
    ]
    brief = _build_volume_brief_text(items, chars_per_volume=400)

    # 锚点（首句）保留
    assert "ch1:天色微亮。" in brief
    assert "ch2:晨雾散去。" in brief
    # 关键事件句（即使在末尾）必须保留
    assert "决定离开京城" in brief
    assert "重伤昏迷" in brief


def test_build_volume_brief_respects_char_budget_and_drops_filler():
    # 大量 filler，仅一个 key 句；预算很紧，filler 不应挤掉 key 句
    long_filler = "街市照常开张，行人来来往往，叫卖声此起彼伏。" * 6
    items = [
        (
            10,
            "破晓时分。" + long_filler + "主角终于决意北上。",
        ),
    ]
    brief = _build_volume_brief_text(items, chars_per_volume=80)

    # 预算严格遵守
    assert len(brief) <= 80 + len("...")
    # 锚点 + key 在限额很紧时仍优先于 filler
    assert "ch10:破晓时分。" in brief
    assert "决意北上" in brief


def test_build_volume_brief_returns_empty_when_no_summaries():
    assert _build_volume_brief_text([], chars_per_volume=200) == ""
    assert _build_volume_brief_text([(1, "")], chars_per_volume=200) == ""


# ---------------------------------------------------------------------------
# get_volume_brief integration with stubbed DB
# ---------------------------------------------------------------------------

class _StubScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return list(self._rows)


class _StubResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _StubScalars(self._rows)


class _StubSession:
    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    def execute(self, _stmt):
        return _StubResult(self._rows)

    def close(self):
        self.closed = True


def test_get_volume_brief_groups_by_volume_and_preserves_key_events(monkeypatch):
    rows = [
        _make_chapter_summary(1, "晨光熹微。市集如常。主角决定北上寻亲。"),
        _make_chapter_summary(2, "马车出城。途中安静。林霜重伤倒在驿道。"),
        _make_chapter_summary(31, "新卷开篇。雪域辽阔。"),
        _make_chapter_summary(32, "主角晋升武宗。"),
    ]
    session = _StubSession(rows)
    monkeypatch.setattr(
        "app.services.memory.summary_manager.SessionLocal",
        lambda: session,
    )

    mgr = SummaryManager()
    brief = mgr.get_volume_brief(
        novel_id=1,
        novel_version_id=1,
        chapter_num=100,  # cutoff = 95, so all 4 rows are included
        volume_size=30,
        chars_per_volume=400,
    )

    # 两卷都应当出现，且各自的关键事件均被保留
    assert "【卷1 (1-2)】" in brief
    assert "【卷2 (31-32)】" in brief
    assert "决定北上寻亲" in brief
    assert "重伤倒在驿道" in brief
    assert "晋升武宗" in brief
    # 显式断言 SessionLocal 被关闭（避免连接泄漏回归）
    assert session.closed is True
