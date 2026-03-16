from app.core.constants import DEFAULT_CHAPTER_WORD_COUNT
from app.services.generation.agents import FinalizerAgent, WriterAgent
from app.services.generation.nodes.finalize import node_finalize
from app.services.generation.nodes.writer import node_writer


def test_writer_agent_passes_word_count_to_render_prompt(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_render(template: str, **kwargs):
        captured["template"] = template
        captured["word_count"] = kwargs.get("word_count")
        return "writer-prompt"

    def _fake_invoke(**_kwargs):
        return "生成正文"

    monkeypatch.setattr("app.services.generation.agents.render_prompt", _fake_render)
    monkeypatch.setattr("app.services.generation.agents.invoke_chapter_body_structured", _fake_invoke)

    out = WriterAgent().run(
        novel_id="novel-1",
        chapter_num=2,
        outline={"title": "第二章"},
        context={"scene": "test"},
        language="zh",
        native_style_profile="默认",
        provider="openai",
        model="gpt-4o-mini",
        word_count=2800,
    )

    assert out == "生成正文"
    assert captured["template"] == "next_chapter"
    assert captured["word_count"] == 2800


def test_finalizer_agent_passes_word_count_to_render_prompt(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_render(template: str, **kwargs):
        captured["template"] = template
        captured["word_count"] = kwargs.get("word_count")
        return "finalizer-prompt"

    def _fake_invoke(**_kwargs):
        return "定稿正文"

    monkeypatch.setattr("app.services.generation.agents.render_prompt", _fake_render)
    monkeypatch.setattr("app.services.generation.agents.invoke_chapter_body_structured", _fake_invoke)

    out = FinalizerAgent().run(
        draft="原始草稿",
        feedback="收紧节奏",
        language="zh",
        provider="openai",
        model="gpt-4o-mini",
        word_count=2800,
    )

    assert out == "定稿正文"
    assert captured["template"] == "finalizer_polish"
    assert captured["word_count"] == 2800


def test_node_writer_passes_default_word_count(monkeypatch):
    class _CaptureWriter:
        def __init__(self):
            self.calls: list[int | None] = []

        def run(
            self,
            novel_id,
            chapter_num,
            outline,
            context,
            language,
            native_style_profile,
            provider,
            model,
            word_count=None,
        ):
            self.calls.append(word_count)
            return "章节正文"

    writer = _CaptureWriter()
    monkeypatch.setattr("app.services.generation.nodes.writer.get_model_for_stage", lambda *_: ("openai", "mock"))
    monkeypatch.setattr("app.services.generation.nodes.writer.snapshot_usage", lambda: {"input_tokens": 0, "output_tokens": 0})
    monkeypatch.setattr("app.services.generation.progress.snapshot_usage", lambda: {"input_tokens": 0, "output_tokens": 0})

    state = {
        "novel_id": 1,
        "strategy": "web-novel",
        "writer": writer,
        "current_chapter": 1,
        "num_chapters": 15,
        "start_chapter": 1,
        "outline": {"chapter_num": 1, "title": "第1章"},
        "context": {},
        "target_language": "zh",
        "native_style_profile": "默认",
        "progress_callback": None,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
    }

    out = node_writer(state)

    assert out["draft"] == "章节正文"
    assert writer.calls == [DEFAULT_CHAPTER_WORD_COUNT]


def test_node_finalize_passes_default_word_count(monkeypatch):
    class _CaptureFinalizer:
        def __init__(self):
            self.calls: list[int | None] = []

        def run(
            self,
            draft,
            feedback,
            language,
            provider,
            model,
            word_count=None,
        ):
            self.calls.append(word_count)
            return "定稿正文"

    class _FactExtractor:
        def run(self, **_kwargs):
            return {}

    class _SummaryMgr:
        def add_summary(self, *_args, **_kwargs):
            return None

    class _CharMgr:
        def get_states(self, *_args, **_kwargs):
            return []

    class _ConsistencyReport:
        def summary(self):
            return "ok"

    class _ScalarResult:
        def scalar_one_or_none(self):
            return None

    class _DummyDB:
        def execute(self, *_args, **_kwargs):
            return _ScalarResult()

        def add(self, *_args, **_kwargs):
            return None

        def commit(self):
            return None

        def rollback(self):
            return None

        def close(self):
            return None

    finalizer = _CaptureFinalizer()
    monkeypatch.setattr("app.services.generation.nodes.finalize.get_model_for_stage", lambda *_: ("openai", "mock"))
    monkeypatch.setattr("app.services.generation.nodes.finalize.SessionLocal", lambda: _DummyDB())
    monkeypatch.setattr("app.services.generation.nodes.finalize.evaluate_language_quality", lambda *_: (1.0, "ok"))
    monkeypatch.setattr("app.services.generation.nodes.finalize.generate_chapter_summary", lambda *_args, **_kwargs: "摘要")
    monkeypatch.setattr("app.services.generation.nodes.finalize.update_character_states_from_content", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.generation.nodes.finalize.update_character_profiles_incremental", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.generation.nodes.finalize.write_longform_artifacts", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.generation.nodes.finalize.snapshot_usage", lambda: {"input_tokens": 0, "output_tokens": 0})
    monkeypatch.setattr("app.services.generation.progress.snapshot_usage", lambda: {"input_tokens": 0, "output_tokens": 0})
    monkeypatch.setattr("app.services.generation.nodes.finalize.chapter_progress_signal", lambda **_kwargs: 1.0)
    monkeypatch.setattr("app.services.generation.nodes.finalize.aesthetic_score", lambda *_args, **_kwargs: 1.0)
    monkeypatch.setattr("app.services.generation.nodes.finalize.persist_resume_runtime_state", lambda *_args, **_kwargs: None)

    state = {
        "novel_id": 1,
        "novel_version_id": 1,
        "current_chapter": 2,
        "num_chapters": 3,
        "start_chapter": 1,
        "end_chapter": 3,
        "strategy": "web-novel",
        "target_language": "zh",
        "draft": "原始草稿",
        "feedback": "收紧节奏",
        "finalizer": finalizer,
        "fact_extractor": _FactExtractor(),
        "outline": {"chapter_num": 2, "title": "第2章"},
        "summary_mgr": _SummaryMgr(),
        "char_mgr": _CharMgr(),
        "prewrite": {},
        "context": {"budget_used": 0},
        "score": 0.8,
        "factual_score": 0.8,
        "aesthetic_review_score": 0.8,
        "consistency_report": _ConsistencyReport(),
        "review_attempt": 0,
        "rerun_count": 0,
        "total_input_tokens": 0,
        "total_output_tokens": 0,
        "closure_state": {},
        "low_progress_streak": 0,
        "decision_state": {},
        "consistency_scorecard": {},
        "review_gate": {},
        "review_suggestions": {},
        "progress_callback": None,
    }

    out = node_finalize(state)

    assert out["quality_passed"] is True
    assert finalizer.calls == [DEFAULT_CHAPTER_WORD_COUNT]
