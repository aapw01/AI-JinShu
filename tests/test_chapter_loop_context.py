from types import SimpleNamespace

from app.services.generation.nodes.chapter_loop import node_load_context
from app.services.generation.nodes.cross_chapter_check import node_cross_chapter_check


def test_node_load_context_reuses_selected_recent_summaries(monkeypatch):
    class _DummyDB:
        def close(self):
            return None

    class _SummaryMgr:
        def __init__(self):
            self.calls = 0

        def get_summaries_before(self, *_args, **_kwargs):
            self.calls += 1
            return [
                {"chapter_num": 4, "summary": "四"},
                {"chapter_num": 5, "summary": "五"},
                {"chapter_num": 6, "summary": "六"},
                {"chapter_num": 7, "summary": "七"},
                {"chapter_num": 8, "summary": "八"},
            ]

    summary_mgr = _SummaryMgr()

    monkeypatch.setattr("app.services.generation.nodes.chapter_loop.SessionLocal", lambda: _DummyDB())
    monkeypatch.setattr("app.services.generation.nodes.chapter_loop.progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.generation.nodes.chapter_loop.chapter_progress", lambda *_args, **_kwargs: 0.1)
    monkeypatch.setattr(
        "app.services.memory.context.build_chapter_context",
        lambda *_args, **_kwargs: {
            "recent_window": "第8章: 八",
            "summaries": [{"chapter_num": 8, "summary": "八"}],
            "anti_repeat_constraints": {},
            "transition_constraints": {},
        },
    )

    state = {
        "novel_id": 1,
        "novel_version_id": 1,
        "current_chapter": 9,
        "num_chapters": 12,
        "start_chapter": 1,
        "prewrite": {},
        "full_outlines": [{"chapter_num": 9, "title": "第9章", "outline": "续写冲突"}],
        "volume_plan": {},
        "closure_state": {},
        "decision_state": {},
        "volume_size": 30,
        "summary_mgr": summary_mgr,
        "bible_store": SimpleNamespace(get_chapter_constraints=lambda *_args, **_kwargs: []),
        "char_mgr": SimpleNamespace(get_states=lambda *_args, **_kwargs: []),
        "progress_callback": None,
        "low_progress_streak": 0,
        "pacing_mode": "normal",
        "total_input_tokens": 0,
        "total_output_tokens": 0,
    }

    out = node_load_context(state)

    assert out["context"]["summaries"] == [{"chapter_num": 8, "summary": "八"}]
    assert summary_mgr.calls == 0


def test_cross_chapter_check_uses_full_recent_summaries(monkeypatch):
    class _Reviewer:
        def __init__(self):
            self.cross_recent = None
            self.unknown_recent = None

        def run_cross_chapter_check(self, **kwargs):
            self.cross_recent = kwargs["recent_summaries"]
            return {"contradictions": []}

        def run_unknown_character_check(self, **kwargs):
            self.unknown_recent = kwargs["recent_summaries"]
            return {"verdicts": []}

    reviewer = _Reviewer()

    monkeypatch.setattr(
        "app.services.generation.nodes.cross_chapter_check.progress",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.generation.nodes.cross_chapter_check.chapter_progress",
        lambda *_args, **_kwargs: 0.58,
    )
    monkeypatch.setattr(
        "app.services.generation.nodes.cross_chapter_check.get_model_for_stage",
        lambda *_args, **_kwargs: ("openai", "mock-reviewer"),
    )
    monkeypatch.setattr(
        "app.services.generation.nodes.cross_chapter_check.get_inference_for_stage",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "app.services.generation.nodes.cross_chapter_check._get_dead_characters",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        "app.services.generation.nodes.cross_chapter_check.extract_unknown_characters",
        lambda *_args, **_kwargs: ["林陌"],
    )

    state = {
        "current_chapter": 9,
        "draft": "林陌推门而入。",
        "strategy": "web-novel",
        "num_chapters": 12,
        "target_language": "zh",
        "context": {
            "summaries": [{"chapter_num": 8, "summary": "八"}],
            "full_recent_summaries": [
                {"chapter_num": 4, "summary": "四"},
                {"chapter_num": 5, "summary": "五"},
                {"chapter_num": 6, "summary": "六"},
                {"chapter_num": 7, "summary": "七"},
                {"chapter_num": 8, "summary": "八"},
            ],
            "character_states": [],
        },
        "prewrite": {"specification": {"characters": [{"name": "林秋"}]}},
        "reviewer": reviewer,
        "review_suggestions": {},
        "review_gate": {},
        "progress_callback": None,
    }

    out = node_cross_chapter_check(state)

    assert out == {}
    assert [item["chapter_num"] for item in reviewer.cross_recent] == [4, 5, 6, 7, 8]
    assert [item["chapter_num"] for item in reviewer.unknown_recent] == [6, 7, 8]
