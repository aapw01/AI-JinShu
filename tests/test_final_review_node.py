"""Tests for node_final_book_review and FinalReviewerAgent.run_full_book."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Test 1: node_final_book_review uses volume reports + recent summaries
# ---------------------------------------------------------------------------

def _make_state(extra=None):
    """Minimal GenerationState-like dict for node_final_book_review."""
    quality_store = MagicMock()
    quality_store.list_reports.return_value = []
    quality_store.add_report.return_value = None

    summary_mgr = MagicMock()
    summary_mgr.get_summaries_before.return_value = []

    bible_store = MagicMock()
    bible_store.get_chapter_constraints.return_value = {"unresolved_foreshadows": []}

    final_reviewer = MagicMock()
    final_reviewer.run_full_book.return_value = {
        "score": 0.8,
        "confidence": 0.9,
        "feedback": "ok",
        "must_fix": [],
        "should_improve": [],
        "fallback": False,
    }

    checkpoint_store = MagicMock()

    state = {
        "novel_id": 1,
        "novel_version_id": 10,
        "end_chapter": 50,
        "book_effective_end_chapter": 50,
        "book_target_total_chapters": 50,
        "segment_start_chapter": 1,
        "segment_end_chapter": 50,
        "volume_no": 1,
        "target_language": "zh",
        "strategy": {},
        "quality_store": quality_store,
        "summary_mgr": summary_mgr,
        "bible_store": bible_store,
        "final_reviewer": final_reviewer,
        "checkpoint_store": checkpoint_store,
        "task_id": "task-1",
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "estimated_cost": 0.0,
    }
    if extra:
        state.update(extra)
    return state


def test_node_final_book_review_uses_volume_reports_not_all_summaries():
    """Verify the node calls list_reports(scope='volume') and get_summaries_before(limit=15),
    and calls final_reviewer.run_full_book with volume_reports/recent_summaries/unresolved_foreshadows."""
    state = _make_state()

    fake_report = SimpleNamespace(
        scope_id="1",
        verdict="pass",
        metrics_json={"avg_review_score": 0.85},
    )
    state["quality_store"].list_reports.return_value = [fake_report]
    recent = [{"chapter_num": 49, "summary": "final arc"}, {"chapter_num": 50, "summary": "ending"}]
    state["summary_mgr"].get_summaries_before.return_value = recent

    with (
        patch("app.services.generation.nodes.final_review.SessionLocal") as mock_session,
        patch("app.services.generation.nodes.final_review.get_model_for_stage", return_value=("openai", "gpt-4")),
        patch("app.services.generation.nodes.final_review.get_inference_for_stage", return_value={}),
        patch("app.services.generation.nodes.final_review.progress"),
        patch("app.services.generation.nodes.final_review.persist_resume_runtime_state"),
        patch("app.services.generation.nodes.final_review.save_prewrite_artifacts"),
    ):
        mock_db = MagicMock()
        mock_session.return_value = mock_db
        # Second SessionLocal call for quality_blocked scan
        mock_db.execute.return_value.scalars.return_value.all.return_value = []

        from app.services.generation.nodes.final_review import node_final_book_review
        node_final_book_review(state)

    # list_reports called with scope="volume"
    state["quality_store"].list_reports.assert_called_once()
    call_kwargs = state["quality_store"].list_reports.call_args
    assert call_kwargs.kwargs.get("scope") == "volume" or call_kwargs.args[1] == "volume"  # positional or keyword

    # get_summaries_before called with limit=15
    state["summary_mgr"].get_summaries_before.assert_called_once()
    sb_kwargs = state["summary_mgr"].get_summaries_before.call_args.kwargs
    assert sb_kwargs.get("limit") == 15

    # run_full_book called with the new kwargs
    state["final_reviewer"].run_full_book.assert_called_once()
    rfb_kwargs = state["final_reviewer"].run_full_book.call_args.kwargs
    assert "volume_reports" in rfb_kwargs
    assert "recent_summaries" in rfb_kwargs
    assert "unresolved_foreshadows" in rfb_kwargs
    # volume_reports built from fake_report
    assert rfb_kwargs["volume_reports"][0]["volume_no"] == "1"
    assert rfb_kwargs["volume_reports"][0]["verdict"] == "pass"
    # recent_summaries passed through
    assert rfb_kwargs["recent_summaries"] == recent


# ---------------------------------------------------------------------------
# Test 2: FinalReviewerAgent.run_full_book accepts new payload
# ---------------------------------------------------------------------------

def test_final_reviewer_run_full_book_accepts_new_payload():
    """FinalReviewerAgent.run_full_book with new signature returns dict with 'score'."""
    from app.services.generation.agents import FinalReviewerAgent

    agent = FinalReviewerAgent()

    fake_llm = MagicMock()
    with (
        patch("app.services.generation.agents.get_llm_with_fallback", return_value=fake_llm),
        patch("app.services.generation.agents.render_prompt", return_value="prompt text"),
        patch(
            "app.services.generation.agents._invoke_json_with_schema",
            return_value={
                "score": 0.75,
                "confidence": 0.8,
                "feedback": "good",
                "must_fix": [],
                "should_improve": [],
                "fallback": False,
            },
        ),
    ):
        result = agent.run_full_book(
            volume_reports=[],
            recent_summaries=[],
            unresolved_foreshadows=[],
            language="zh",
        )

    assert isinstance(result, dict)
    assert "score" in result
    assert result["score"] == 0.75


# ---------------------------------------------------------------------------
# Test 3: Prompt template renders volume_reports / recent_summaries / unresolved_foreshadows
# ---------------------------------------------------------------------------

def test_final_book_review_prompt_renders_volume_reports():
    """Prompt template renders all three input sections correctly."""
    from app.prompts import render_prompt

    volume_reports = [
        {"volume_no": "1", "verdict": "pass", "metrics": {"avg_review_score": 0.85, "avg_language_score": 0.9}},
        {"volume_no": "2", "verdict": "warning", "metrics": {"avg_review_score": 0.6}},
    ]
    recent_summaries = [
        {"chapter_num": 99, "summary": "主角战胜BOSS"},
        {"chapter_num": 100, "summary": "大结局收束"},
    ]
    unresolved_foreshadows = [
        {"title": "神秘钥匙", "foreshadow_id": "fs-001", "planted_chapter": 3},
    ]

    prompt = render_prompt(
        "final_book_review",
        volume_reports=volume_reports,
        recent_summaries=recent_summaries,
        unresolved_foreshadows=unresolved_foreshadows,
        language="zh",
    )

    assert "卷1" in prompt
    assert "verdict=pass" in prompt
    assert "卷2" in prompt
    assert "verdict=warning" in prompt
    assert "第99章" in prompt
    assert "主角战胜BOSS" in prompt
    assert "第100章" in prompt
    assert "神秘钥匙" in prompt
    assert "第3章" in prompt
