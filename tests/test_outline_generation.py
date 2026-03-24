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
    def test_batches_large_volume_generation(self):
        agent = OutlinerAgent()
        batch_calls: list[tuple[int, int]] = []

        def _fake_batch(*, start_chapter, num_chapters, **_kwargs):
            batch_calls.append((start_chapter, num_chapters))
            return [
                {
                    "chapter_num": i,
                    "title": f"第{i}章：测试内容",
                    "outline": "主角发现了关键线索，决定采取行动推进主线",
                }
                for i in range(start_chapter, start_chapter + num_chapters)
            ]

        with patch.object(agent, "_generate_outline_batch", side_effect=_fake_batch):
            with patch.object(agent, "_harmonize_volume_outlines", side_effect=lambda **kwargs: kwargs["outlines"]):
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
        assert batch_calls == [(1, 10), (11, 10), (21, 10)]

    def test_raises_on_batch_generation_failure(self):
        """批次生成失败时应直接抛错，不再回退为空大纲"""
        agent = OutlinerAgent()
        with patch.object(agent, "_generate_outline_batch", side_effect=Exception("LLM error")):
            with pytest.raises(Exception, match="LLM error"):
                agent.run_volume_outlines(
                    novel_id="test",
                    volume_no=1,
                    start_chapter=1,
                    num_chapters=5,
                    prewrite={},
                    previous_summaries=[],
                )


class TestGenerateOutlineBatch:
    """_generate_outline_batch 的重试与推断补全逻辑"""

    def _make_agent_and_llm(self):
        from unittest.mock import MagicMock
        from app.services.generation.agents import OutlinerAgent
        agent = OutlinerAgent()
        mock_llm = MagicMock()
        return agent, mock_llm

    def _make_outline(self, chapter_num: int) -> dict:
        return {
            "chapter_num": chapter_num,
            "title": f"第{chapter_num}章：测试",
            "outline": "主角发现了关键线索，决定采取行动推进主线剧情",
        }

    def test_retries_when_count_short_then_succeeds(self):
        """首次返回 9 条，重试后返回 10 条，应成功"""
        agent, _ = self._make_agent_and_llm()
        short = [self._make_outline(i) for i in range(1, 10)]   # 9 items
        full  = [self._make_outline(i) for i in range(1, 11)]   # 10 items
        calls = iter([{"outlines": short}, {"outlines": full}])

        with patch("app.services.generation.agents._invoke_json_with_schema", side_effect=lambda *a, **kw: next(calls)):
            with patch("app.services.generation.agents.get_llm_with_fallback"):
                result = agent._generate_outline_batch(
                    novel_id="1", volume_no=1, start_chapter=1, num_chapters=10,
                    prewrite={}, previous_summaries=[], planning_context={},
                    language="zh", provider=None, model=None,
                )
        assert len(result) == 10
        assert result[0]["chapter_num"] == 1
        assert result[9]["chapter_num"] == 10

    def test_infers_missing_chapter_when_short_by_one_after_retries(self):
        """3 次重试后仍少 1 章，应推断补全而不是抛错"""
        agent, _ = self._make_agent_and_llm()
        # Missing chapter 5 — return chapters 1-4, 6-10
        partial = [self._make_outline(i) for i in list(range(1, 5)) + list(range(6, 11))]
        assert len(partial) == 9

        with patch("app.services.generation.agents._invoke_json_with_schema", return_value={"outlines": partial}):
            with patch("app.services.generation.agents.get_llm_with_fallback"):
                result = agent._generate_outline_batch(
                    novel_id="1", volume_no=1, start_chapter=1, num_chapters=10,
                    prewrite={}, previous_summaries=[], planning_context={},
                    language="zh", provider=None, model=None,
                )
        assert len(result) == 10
        chap5 = next(r for r in result if r["chapter_num"] == 5)
        assert chap5["outline"]  # inferred outline must be non-empty

    def test_raises_when_missing_more_than_two_after_retries(self):
        """3 次重试后缺 3 章以上，应抛错"""
        agent, _ = self._make_agent_and_llm()
        partial = [self._make_outline(i) for i in range(1, 8)]  # only 7 of 10

        with patch("app.services.generation.agents._invoke_json_with_schema", return_value={"outlines": partial}):
            with patch("app.services.generation.agents.get_llm_with_fallback"):
                with pytest.raises(ValueError, match="expected=10 actual=7"):
                    agent._generate_outline_batch(
                        novel_id="1", volume_no=1, start_chapter=1, num_chapters=10,
                        prewrite={}, previous_summaries=[], planning_context={},
                        language="zh", provider=None, model=None,
                    )

    def test_chapter_nums_are_sequential(self):
        agent = OutlinerAgent()
        def _fake_batch(*, start_chapter, num_chapters, **_kwargs):
            return [
                {"chapter_num": i, "title": f"第{i}章：内容", "outline": "足够长的大纲内容测试数据，主角发现了隐藏的真相并决定行动"}
                for i in range(start_chapter, start_chapter + num_chapters)
            ]

        with patch.object(agent, "_generate_outline_batch", side_effect=_fake_batch):
            with patch.object(agent, "_harmonize_volume_outlines", side_effect=lambda **kwargs: kwargs["outlines"]):
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

    def test_reuses_valid_db_batches_and_only_generates_missing_batches(self):
        agent = OutlinerAgent()
        existing = [
            {"chapter_num": i, "title": f"第{i}章：已有", "outline": "这是已存在且有效的大纲内容，足以复用"}
            for i in range(1, 11)
        ]
        batch_calls: list[tuple[int, int]] = []

        def _fake_batch(*, start_chapter, num_chapters, **_kwargs):
            batch_calls.append((start_chapter, num_chapters))
            return [
                {"chapter_num": i, "title": f"第{i}章：新批次", "outline": "足够长的大纲内容测试数据，主角发现了隐藏的真相并决定行动"}
                for i in range(start_chapter, start_chapter + num_chapters)
            ]

        with patch("app.services.generation.agents.load_outlines_from_db", return_value=existing):
            with patch("app.services.generation.agents.save_full_outlines"):
                with patch.object(agent, "_generate_outline_batch", side_effect=_fake_batch):
                    with patch.object(agent, "_harmonize_volume_outlines", side_effect=lambda **kwargs: kwargs["outlines"]):
                        result = agent.run_volume_outlines(
                            novel_id="1",
                            novel_version_id=1,
                            volume_no=1,
                            start_chapter=1,
                            num_chapters=30,
                            prewrite={},
                            previous_summaries=[],
                        )
        assert len(result) == 30
        assert batch_calls == [(11, 10), (21, 10)]

    def test_harmonization_failure_soft_fails(self):
        agent = OutlinerAgent()

        def _fake_batch(*, start_chapter, num_chapters, **_kwargs):
            return [
                {
                    "chapter_num": i,
                    "title": f"第{i}章：测试内容",
                    "outline": "主角发现了关键线索，决定采取行动推进主线",
                }
                for i in range(start_chapter, start_chapter + num_chapters)
            ]

        with patch.object(agent, "_generate_outline_batch", side_effect=_fake_batch):
            with patch.object(agent, "_harmonize_volume_outlines", side_effect=Exception("harmonize failed")):
                result = agent.run_volume_outlines(
                    novel_id="test",
                    volume_no=1,
                    start_chapter=1,
                    num_chapters=12,
                    prewrite={},
                    previous_summaries=[],
                )
        assert len(result) == 12


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


class TestGenerateNextVolumeOutlinesIfNeeded:
    """Tests for the _generate_next_volume_outlines_if_needed helper"""

    def _make_state(self, volume_size=30, book_end=60):
        summary_mgr = MagicMock()
        summary_mgr.get_summaries_before.return_value = []
        return {
            "novel_id": 1,
            "novel_version_id": 1,
            "volume_size": volume_size,
            "book_effective_end_chapter": book_end,
            "end_chapter": book_end,
            "strategy": "web-novel",
            "target_language": "zh",
            "prewrite": {},
            "outliner": MagicMock(),
            "summary_mgr": summary_mgr,
        }

    def test_skips_when_last_volume(self):
        """Should not generate when next_vol_start > book_end (already on last volume)"""
        from app.services.generation.nodes.volume import _generate_next_volume_outlines_if_needed

        state = self._make_state(volume_size=30, book_end=30)
        current_vol_end = 30  # last chapter = book end

        with patch("app.services.generation.common.load_outlines_from_db", return_value=[]):
            with patch("app.services.generation.common.save_full_outlines") as mock_save:
                _generate_next_volume_outlines_if_needed(state, 1, current_vol_end, {})

        state["outliner"].run_volume_outlines.assert_not_called()
        mock_save.assert_not_called()

    def test_skips_when_outlines_already_valid(self):
        """Should not generate when next volume outlines already exist and are valid"""
        from app.services.generation.nodes.volume import _generate_next_volume_outlines_if_needed

        state = self._make_state(volume_size=30, book_end=60)
        existing_outlines = [
            {"chapter_num": i, "outline": "这是已有的有效大纲内容超过十个字符"}
            for i in range(31, 61)
        ]

        with patch("app.services.generation.common.load_outlines_from_db", return_value=existing_outlines):
            with patch("app.services.generation.common.save_full_outlines") as mock_save:
                _generate_next_volume_outlines_if_needed(state, 1, 30, {})

        state["outliner"].run_volume_outlines.assert_not_called()
        mock_save.assert_not_called()

    def test_generates_when_outlines_missing(self):
        """Should generate next volume outlines when they are missing"""
        from app.services.generation.nodes.volume import _generate_next_volume_outlines_if_needed

        state = self._make_state(volume_size=30, book_end=60)
        state["outliner"].run_volume_outlines.return_value = [
            {"chapter_num": i, "outline": "生成的大纲内容测试数据"}
            for i in range(31, 61)
        ]

        with patch("app.services.generation.common.load_outlines_from_db", return_value=[]):
            with patch("app.services.generation.common.save_full_outlines") as mock_save:
                _generate_next_volume_outlines_if_needed(state, 1, 30, {})

        state["outliner"].run_volume_outlines.assert_called_once()
        call_kwargs = state["outliner"].run_volume_outlines.call_args[1]
        assert call_kwargs["start_chapter"] == 31
        assert call_kwargs["volume_no"] == 2
        assert "previous_summaries" in call_kwargs
        mock_save.assert_called_once()


class TestNodeRefineChapterOutline:
    def _make_state(self, chapter_num=5, outline=None):
        summary_mgr = MagicMock()
        summary_mgr.get_summaries_before.return_value = [
            {"chapter_num": 4, "summary": "主角在追逐中发现了隐藏的线索并与对手正面交锋"}
        ]
        return {
            "novel_id": 1,
            "novel_version_id": 1,
            "current_chapter": chapter_num,
            "outline": outline or {"chapter_num": chapter_num, "outline": "测试大纲内容", "hook": "old hook"},
            "strategy": "web-novel",
            "summary_mgr": summary_mgr,
        }

    def test_skips_refinement_for_early_chapters(self):
        """章节 < 3 时跳过精化，直接返回原大纲"""
        from app.services.generation.nodes.chapter_loop import node_refine_chapter_outline
        state = self._make_state(chapter_num=2)
        result = node_refine_chapter_outline(state)
        assert result["outline"] == state["outline"]
        state["summary_mgr"].get_summaries_before.assert_not_called()

    def test_returns_original_outline_when_no_summaries(self):
        """无摘要时返回原始大纲"""
        from app.services.generation.nodes.chapter_loop import node_refine_chapter_outline
        state = self._make_state(chapter_num=5)
        state["summary_mgr"].get_summaries_before.return_value = []
        result = node_refine_chapter_outline(state)
        assert result["outline"]["hook"] == "old hook"

    def test_applies_refinable_fields(self):
        """LLM 返回有效字段时应合并到 outline"""
        from app.services.generation.nodes.chapter_loop import node_refine_chapter_outline
        state = self._make_state(chapter_num=5)
        refined = {"hook": "新钩子内容", "opening_scene": "审讯室场景"}
        with patch("app.prompts.render_prompt", return_value="prompt"):
            with patch("app.services.generation.nodes.chapter_loop._invoke_json_simple", return_value=refined):
                with patch("app.core.llm.get_llm_with_fallback", return_value=MagicMock()):
                    with patch("app.core.strategy.get_model_for_stage", return_value=(None, None)):
                        result = node_refine_chapter_outline(state)
        assert result["outline"]["hook"] == "新钩子内容"
        assert result["outline"]["opening_scene"] == "审讯室场景"

    def test_does_not_modify_core_fields(self):
        """LLM 不得覆盖核心字段 outline/chapter_objective"""
        from app.services.generation.nodes.chapter_loop import node_refine_chapter_outline
        outline = {
            "chapter_num": 5, "outline": "核心大纲不得改动",
            "chapter_objective": "主线目标", "hook": "old hook"
        }
        state = self._make_state(chapter_num=5, outline=outline)
        # LLM tries to overwrite protected fields - should be ignored
        refined = {"outline": "被替换的大纲", "chapter_objective": "替换目标", "hook": "新钩子"}
        with patch("app.prompts.render_prompt", return_value="prompt"):
            with patch("app.services.generation.nodes.chapter_loop._invoke_json_simple", return_value=refined):
                with patch("app.core.llm.get_llm_with_fallback", return_value=MagicMock()):
                    with patch("app.core.strategy.get_model_for_stage", return_value=(None, None)):
                        result = node_refine_chapter_outline(state)
        assert result["outline"]["outline"] == "核心大纲不得改动"
        assert result["outline"]["chapter_objective"] == "主线目标"
        # hook IS in _REFINABLE_FIELDS so it should be updated
        assert result["outline"]["hook"] == "新钩子"

    def test_returns_original_on_llm_failure(self):
        """LLM 调用失败时不崩溃，返回原始大纲"""
        from app.services.generation.nodes.chapter_loop import node_refine_chapter_outline
        state = self._make_state(chapter_num=5)
        with patch("app.prompts.render_prompt", side_effect=Exception("LLM error")):
            result = node_refine_chapter_outline(state)
        assert result["outline"]["hook"] == "old hook"
