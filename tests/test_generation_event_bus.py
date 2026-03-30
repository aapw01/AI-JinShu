from __future__ import annotations

import pytest

from app.services.generation.chapter_commit import (
    _build_chapter_finalized_event_bus,
    write_longform_artifacts,
)
from app.services.generation.events import EventBus, EventHandlerError, GenerationEvent


def test_event_bus_optional_handler_failure_does_not_raise():
    bus = EventBus()
    called: list[str] = []

    def _required(event: GenerationEvent) -> None:
        called.append(f"required:{event.name}")

    def _optional(_event: GenerationEvent) -> None:
        raise RuntimeError("optional boom")

    bus.register("chapter.finalized", _required, required=True)
    bus.register("chapter.finalized", _optional, required=False)

    result = bus.dispatch(GenerationEvent(name="chapter.finalized", payload={"chapter_num": 1}))

    assert called == ["required:chapter.finalized"]
    assert len(result["failures"]) == 1
    assert result["failures"][0]["required"] is False


def test_event_bus_required_handler_failure_raises():
    bus = EventBus()

    def _required(_event: GenerationEvent) -> None:
        raise RuntimeError("required boom")

    bus.register("chapter.finalized", _required, required=True)

    with pytest.raises(EventHandlerError):
        bus.dispatch(GenerationEvent(name="chapter.finalized", payload={"chapter_num": 1}))


def test_write_longform_artifacts_optional_handler_failure_does_not_raise(monkeypatch):
    dispatched: dict[str, object] = {}

    class _Bus:
        def dispatch(self, event: GenerationEvent):
            dispatched["name"] = event.name
            dispatched["payload"] = event.payload
            return {"failures": [{"handler": "optional_handler", "required": False, "error": "boom"}]}

    monkeypatch.setattr(
        "app.services.generation.chapter_commit._build_chapter_finalized_event_bus",
        lambda: _Bus(),
    )

    state = {
        "novel_id": 1,
        "novel_version_id": 2,
        "volume_no": 1,
        "segment_end_chapter": 99,
        "end_chapter": 99,
        "bible_store": object(),
        "quality_store": object(),
        "checkpoint_store": object(),
    }

    write_longform_artifacts(
        state,  # type: ignore[arg-type]
        chapter_num=1,
        summary_text="摘要",
        final_content="正文",
        language_score=0.8,
        aesthetic_score_val=0.8,
        revision_count=0,
    )

    assert dispatched["name"] == "chapter.finalized"
    payload = dispatched["payload"]
    assert isinstance(payload, dict)
    assert payload["chapter_num"] == 1
    assert payload["summary_text"] == "摘要"


def test_write_longform_artifacts_required_handler_failure_raises(monkeypatch):
    class _Bus:
        def dispatch(self, _event: GenerationEvent):
            raise EventHandlerError("required boom")

    monkeypatch.setattr(
        "app.services.generation.chapter_commit._build_chapter_finalized_event_bus",
        lambda: _Bus(),
    )

    state = {
        "novel_id": 1,
        "novel_version_id": 2,
        "volume_no": 1,
        "segment_end_chapter": 99,
        "end_chapter": 99,
        "bible_store": object(),
        "quality_store": object(),
        "checkpoint_store": object(),
    }

    with pytest.raises(EventHandlerError):
        write_longform_artifacts(
            state,  # type: ignore[arg-type]
            chapter_num=1,
            summary_text="摘要",
            final_content="正文",
            language_score=0.8,
            aesthetic_score_val=0.8,
            revision_count=0,
        )


def test_chapter_finalized_quality_handler_is_required(monkeypatch):
    monkeypatch.setattr(
        "app.services.generation.chapter_commit._chapter_finalized_story_bible_handler",
        lambda _event: None,
    )
    monkeypatch.setattr(
        "app.services.generation.chapter_commit._chapter_finalized_progression_handler",
        lambda _event: None,
    )
    monkeypatch.setattr(
        "app.services.generation.chapter_commit._chapter_finalized_checkpoint_handler",
        lambda _event: None,
    )
    monkeypatch.setattr(
        "app.services.generation.chapter_commit._chapter_finalized_telemetry_handler",
        lambda _event: None,
    )

    def _explode(_event: GenerationEvent) -> None:
        raise RuntimeError("quality failed")

    monkeypatch.setattr(
        "app.services.generation.chapter_commit._chapter_finalized_quality_report_handler",
        _explode,
    )

    bus = _build_chapter_finalized_event_bus()

    with pytest.raises(EventHandlerError):
        bus.dispatch(GenerationEvent(name="chapter.finalized", payload={"chapter_num": 1}))
