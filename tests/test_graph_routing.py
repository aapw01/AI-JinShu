"""Tests for LangGraph routing functions — no LLM / DB calls needed."""
import pytest

from app.services.generation.consistency import ConsistencyReport, ConsistencyIssue
from app.services.generation.langgraph_pipeline import (
    _node_volume_replan,
    _node_writer,
    _route_consistency,
    _route_after_confirmation,
    _route_finalize,
    _route_review,
)


def _base_state(**overrides):
    state = {
        "novel_id": 1,
        "num_chapters": 5,
        "start_chapter": 1,
        "current_chapter": 1,
        "end_chapter": 5,
        "score": 0.0,
        "review_attempt": 0,
        "rerun_count": 0,
    }
    state.update(overrides)
    return state


# ---- _route_consistency ------------------------------------------------------

class TestRouteConsistency:
    def test_beats_when_passed(self):
        report = ConsistencyReport(passed=True)
        assert _route_consistency(_base_state(consistency_report=report)) == "beats"

    def test_beats_when_not_passed(self):
        # P0-A: consistency failures are always soft-failed; never route to save_blocked
        report = ConsistencyReport(
            issues=[ConsistencyIssue("blocker", "character", "角色已死亡")],
            passed=False,
        )
        assert _route_consistency(_base_state(consistency_report=report)) == "beats"


# ---- _route_review -----------------------------------------------------------

class TestRouteReview:
    def test_finalize_on_good_score(self):
        assert _route_review(_base_state(score=0.8)) == "finalizer"

    def test_finalize_when_gate_accepts_minor_polish(self):
        assert _route_review(_base_state(score=0.3, review_gate={"decision": "accept_with_minor_polish"})) == "finalizer"

    def test_finalize_on_exact_threshold(self):
        assert _route_review(_base_state(score=0.7)) == "finalizer"

    def test_revise_when_retries_remain(self):
        assert _route_review(_base_state(score=0.3, review_attempt=1)) == "revise"

    def test_rollback_after_retries_exhausted(self):
        assert _route_review(_base_state(score=0.3, review_attempt=2, rerun_count=0)) == "rollback_rerun"

    def test_force_finalize_after_rollback(self):
        assert _route_review(_base_state(score=0.3, review_attempt=2, rerun_count=1)) == "finalizer"


class TestVolumeRouting:
    def test_after_confirmation_volume_replan_on_volume_start(self):
        state = _base_state(current_chapter=1, end_chapter=10, volume_size=3, start_chapter=1)
        assert _route_after_confirmation(state) == "volume_replan"

    def test_finalize_route_to_rerun_when_quality_failed(self):
        state = _base_state(quality_passed=False, rerun_count=0)
        assert _route_finalize(state) == "rollback_rerun"


class _DummyReport:
    def __init__(self, metrics_json, verdict="warning"):
        self.metrics_json = metrics_json
        self.verdict = verdict


class _DummySnapshot:
    def __init__(self, snapshot_json):
        self.snapshot_json = snapshot_json


class _DummyQualityStore:
    def list_reports(self, novel_id, scope, scope_id=None, novel_version_id=None, db=None, limit=None):
        if scope == "volume" and scope_id == "1":
            return [
                _DummyReport(
                    {
                        "avg_review_score": 0.62,
                        "avg_language_score": 0.6,
                        "avg_aesthetic_score": 0.59,
                        "evidence_chain": [{"metric": "avg_language_score", "value": 0.6, "threshold": 0.65, "status": "warning"}],
                    },
                    verdict="fail",
                )
            ]
        return []


class _DummyBibleStore:
    def get_latest_snapshot(self, novel_id, volume_no, novel_version_id=None):
        if volume_no == 1:
            return _DummySnapshot({"chapter_end": 30, "avg_review_score": 0.62})
        return None

    def get_chapter_constraints(self, novel_id, chapter_num, novel_version_id=None):
        return {
            "unresolved_foreshadows": [
                {"foreshadow_id": "F-001", "title": "神秘石碑", "planted_chapter": 28},
            ]
        }


class _DummyCheckpointStore:
    def save_checkpoint(self, **kwargs):  # pragma: no cover - behavior not asserted here
        return None


class TestVolumeReplanNode:
    def test_volume_replan_includes_quality_focus_and_carry_over(self, monkeypatch):
        class _DummyResult:
            def scalars(self):
                return self

            def all(self):
                return []

        class _DummySession:
            def execute(self, _stmt):
                return _DummyResult()

            def close(self):
                return None

        monkeypatch.setattr("app.services.generation.nodes.volume.SessionLocal", lambda: _DummySession())
        monkeypatch.setattr("app.services.generation.nodes.volume._generate_next_volume_outlines_if_needed", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(
            "app.services.generation.common.load_outlines_from_db",
            lambda *_args, **_kwargs: [
                {"chapter_num": 31, "title": "新卷开篇", "purpose": "建立新矛盾"},
                {"chapter_num": 32, "title": "暗线推进", "purpose": "推进暗线"},
            ],
        )
        state = _base_state(
            novel_id=1,
            current_chapter=31,
            end_chapter=90,
            start_chapter=1,
            volume_size=30,
            num_chapters=90,
            full_outlines=[
                {"chapter_num": 31, "title": "新卷开篇", "purpose": "建立新矛盾"},
                {"chapter_num": 32, "title": "暗线推进", "purpose": "推进暗线"},
            ],
            quality_store=_DummyQualityStore(),
            bible_store=_DummyBibleStore(),
            checkpoint_store=_DummyCheckpointStore(),
            task_id=None,
            progress_callback=None,
        )
        out = _node_volume_replan(state)
        assert out["volume_no"] == 2
        assert out["volume_plan"]["carry_over_foreshadows"][0]["foreshadow_id"] == "F-001"
        assert out["volume_plan"]["quality_focus"]
        assert "质量修正" in out["volume_plan"]["chapter_targets"][0]["goal"]
        assert out["volume_plan"]["replan_level"] == "aggressive"
        assert out["volume_plan"]["gate_evidence"]


class _DummyReviewer:
    def run(self, draft, chapter_num, language, native_style_profile, provider, model, inference=None):
        return (0.75 if "A文" in draft else 0.7), "结构反馈"

    def run_factual(self, draft, chapter_num, context, language, provider, model, inference=None):
        return (0.9 if "A文" in draft else 0.6), "事实反馈", []

    def run_aesthetic(self, draft, chapter_num, language, provider, model, inference=None):
        return (0.65 if "A文" in draft else 0.95), "审美反馈", []




class _AlwaysFailWriter:
    def run(self, novel_id, chapter_num, outline, context, language, native_style_profile, provider, model, inference=None, word_count=None):
        variant = context.get("ab_variant") or "?"
        raise RuntimeError(f"writer generation failed for chapter={chapter_num} provider={provider} model={model} variant={variant}")


class TestNodeWriterFailures:
    def test_writer_error_surfaces_both_ab_variant_details(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.generation.nodes.writer.resolve_ai_profile",
            lambda *_args, **_kwargs: {
                "provider": "openai",
                "model": "mock-writer",
                "inference": {},
                "resolution_trace": ["test"],
            },
        )
        state = _base_state(
            current_chapter=1,
            strategy="web-novel",
            writer=_AlwaysFailWriter(),
            outline={"chapter_num": 1, "title": "开篇"},
            context={"foo": "bar"},
            target_language="zh",
            native_style_profile="",
            progress_callback=None,
        )

        with pytest.raises(RuntimeError) as exc_info:
            _node_writer(state)

        msg = str(exc_info.value)
        assert "writer failed for both variants" in msg
        assert "A=writer generation failed for chapter=1" in msg
        assert "variant=A" in msg
        assert "variant=B" in msg
