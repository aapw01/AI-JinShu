import pytest
from unittest.mock import MagicMock, patch
from app.services.generation.common import _looks_like_meta_text, is_effective_title, is_outline_content_valid
from app.services.generation.agents import OutlinerAgent


class TestLooksLikeMetaText:
    def test_catches_auto_generated(self):
        assert _looks_like_meta_text("auto-generated outline") is True

    def test_catches_auto_generated_out(self):
        assert _looks_like_meta_text("auto-generated out") is True

    def test_catches_placeholder(self):
        assert _looks_like_meta_text("placeholder") is True

    def test_catches_todo(self):
        assert _looks_like_meta_text("TODO: fill in") is True

    def test_catches_tbd(self):
        assert _looks_like_meta_text("TBD") is True

    def test_allows_real_title(self):
        assert _looks_like_meta_text("铁幕之下") is False

    def test_allows_english_real_title(self):
        assert _looks_like_meta_text("The Storm Breaks") is False

    def test_catches_fill_in(self):
        assert _looks_like_meta_text("fill in the content") is True

    def test_catches_insert_here(self):
        assert _looks_like_meta_text("insert here") is True

    def test_empty_string(self):
        assert _looks_like_meta_text("") is False


class TestIsEffectiveTitle:
    def test_rejects_auto_generated_out(self):
        assert is_effective_title("第1章：auto-generated out", 1) is False

    def test_rejects_bare_chapter_num(self):
        assert is_effective_title("第5章", 5) is False

    def test_accepts_real_title(self):
        assert is_effective_title("第1章：铁幕之下", 1) is True

    def test_rejects_chapter_with_placeholder_suffix(self):
        assert is_effective_title("第1章：auto-generated", 1) is False


class TestOutlineContentValid:
    def test_empty_outline_is_invalid(self):
        item = {"chapter_num": 1, "title": "第1章", "outline": ""}
        assert is_outline_content_valid(item) is False

    def test_auto_generated_outline_is_invalid(self):
        item = {"chapter_num": 1, "title": "第1章", "outline": "auto-generated outline"}
        assert is_outline_content_valid(item) is False

    def test_valid_outline(self):
        item = {"chapter_num": 1, "title": "第1章：铁幕之下", "outline": "主角在审讯室中发现线索"}
        assert is_outline_content_valid(item) is True

    def test_outline_too_short_is_invalid(self):
        item = {"chapter_num": 1, "title": "第1章", "outline": "推进"}
        assert is_outline_content_valid(item) is False

    def test_none_outline_is_invalid(self):
        item = {"chapter_num": 1, "title": "第1章", "outline": None}
        assert is_outline_content_valid(item) is False

    def test_non_dict_input_is_invalid(self):
        assert is_outline_content_valid(None) is False  # type: ignore[arg-type]
        assert is_outline_content_valid("string") is False  # type: ignore[arg-type]


class TestRunVolumeOutlines:
    def test_returns_correct_count(self):
        agent = OutlinerAgent()
        mock_data = {
            "outlines": [
                {
                    "chapter_num": i,
                    "title": f"第{i}章：测试内容",
                    "outline": "主角发现了关键线索，决定采取行动推进主线",
                }
                for i in range(1, 31)
            ]
        }
        with patch("app.services.generation.agents._invoke_json_with_schema", return_value=mock_data):
            with patch("app.services.generation.agents.get_llm_with_fallback", return_value=MagicMock()):
                result = agent.run_volume_outlines(
                    novel_id="test",
                    volume_no=1,
                    start_chapter=1,
                    num_chapters=30,
                    prewrite={"global_bible": "test"},
                    previous_summaries=[],
                )
        assert len(result) == 30
        assert result[0]["chapter_num"] == 1
        assert result[29]["chapter_num"] == 30

    def test_returns_fallback_on_llm_failure(self):
        """失败时返回空 outline，不抛异常"""
        agent = OutlinerAgent()
        with patch("app.services.generation.agents._invoke_json_with_schema", side_effect=Exception("LLM error")):
            with patch("app.services.generation.agents.get_llm_with_fallback", return_value=MagicMock()):
                result = agent.run_volume_outlines(
                    novel_id="test",
                    volume_no=1,
                    start_chapter=1,
                    num_chapters=5,
                    prewrite={},
                    previous_summaries=[],
                )
        assert len(result) == 5
        for item in result:
            assert item.get("outline", "") == ""

    def test_chapter_nums_are_sequential(self):
        agent = OutlinerAgent()
        mock_data = {
            "outlines": [
                {"chapter_num": i, "title": f"第{i}章：内容", "outline": "足够长的大纲内容测试数据，主角发现了隐藏的真相并决定行动"}
                for i in range(31, 61)
            ]
        }
        with patch("app.services.generation.agents._invoke_json_with_schema", return_value=mock_data):
            with patch("app.services.generation.agents.get_llm_with_fallback", return_value=MagicMock()):
                result = agent.run_volume_outlines(
                    novel_id="test",
                    volume_no=2,
                    start_chapter=31,
                    num_chapters=30,
                    prewrite={},
                    previous_summaries=[{"chapter_num": 30, "summary": "上一章摘要"}],
                )
        assert result[0]["chapter_num"] == 31
        assert result[-1]["chapter_num"] == 60

    def test_returns_fallback_on_empty_outlines(self):
        """LLM 返回空 outlines 列表时应触发 fallback"""
        agent = OutlinerAgent()
        with patch("app.services.generation.agents._invoke_json_with_schema", return_value={"outlines": []}):
            with patch("app.services.generation.agents.get_llm_with_fallback", return_value=MagicMock()):
                result = agent.run_volume_outlines(
                    novel_id="test",
                    volume_no=1,
                    start_chapter=1,
                    num_chapters=3,
                    prewrite={},
                    previous_summaries=[],
                )
        assert len(result) == 3
        for item in result:
            assert item.get("outline", "") == ""
