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


class TestNodeOutlineFirstVolumeOnly:
    """node_outline 在全新生成时只应生成第一卷数量的大纲，而不是全书"""

    def _make_minimal_state(self, num_chapters=60, volume_size=30):
        from app.services.memory.character_state import CharacterStateManager
        from app.services.memory.progression_state import ProgressionMemoryManager
        from app.services.memory.story_bible import CheckpointStore, QualityReportStore, StoryBibleStore
        from app.services.memory.summary_manager import SummaryManager
        return {
            "novel_id": 1,
            "novel_version_id": 1,
            "start_chapter": 1,
            "end_chapter": num_chapters,
            "num_chapters": num_chapters,
            "segment_start_chapter": 1,
            "segment_end_chapter": num_chapters,
            "segment_target_chapters": num_chapters,
            "book_start_chapter": 1,
            "book_target_total_chapters": num_chapters,
            "book_effective_end_chapter": num_chapters,
            "strategy": "web-novel",
            "target_language": "zh",
            "native_style_profile": "default",
            "volume_size": volume_size,
            "volume_no": 1,
            "prewrite": {"global_bible": "test"},
            "volume_plan": {},
            "segment_plan": None,
            "retry_resume_chapter": 1,
            "current_chapter": 1,
            "next_chapter": 1,
            "creation_task_id": None,
            "outliner": MagicMock(),
            "writer": MagicMock(),
            "reviewer": MagicMock(),
            "finalizer": MagicMock(),
            "fact_extractor": MagicMock(),
            "progression_agent": MagicMock(),
            "final_reviewer": MagicMock(),
            "summary_mgr": SummaryManager(),
            "char_mgr": CharacterStateManager(),
            "progression_mgr": ProgressionMemoryManager(),
            "bible_store": StoryBibleStore(),
            "quality_store": QualityReportStore(),
            "checkpoint_store": CheckpointStore(),
        }

    def test_calls_run_volume_outlines_not_run_full_book(self):
        """node_outline 生成新大纲时应调用 run_volume_outlines，不调用 run_full_book"""
        from app.services.generation.nodes.init_node import node_outline
        state = self._make_minimal_state(num_chapters=60, volume_size=30)

        mock_outlines = [
            {"chapter_num": i, "outline": "主角发现了关键线索并决定采取行动推进剧情", "title": f"第{i}章：测试"}
            for i in range(1, 31)
        ]
        state["outliner"].run_volume_outlines.return_value = mock_outlines

        with patch("app.services.generation.nodes.init_node.load_outlines_from_db", return_value=[]):
            with patch("app.services.generation.nodes.init_node.save_full_outlines"):
                with patch("app.services.generation.nodes.init_node.build_segment_plan", return_value={}):
                    with patch("app.services.generation.nodes.init_node.persist_resume_runtime_state"):
                        with patch("app.services.generation.nodes.init_node.progress"):
                            node_outline(state)

        state["outliner"].run_volume_outlines.assert_called_once()
        call_kwargs = state["outliner"].run_volume_outlines.call_args[1]
        assert call_kwargs["num_chapters"] == 30, f"Expected 30, got {call_kwargs['num_chapters']}"
        assert call_kwargs["volume_no"] == 1
        state["outliner"].run_full_book.assert_not_called()
