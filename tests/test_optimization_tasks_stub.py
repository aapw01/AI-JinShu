"""Optimization Celery task wrappers (mix of stubs + real wired)."""

from __future__ import annotations

import pytest

from app.core import feature_flags
from app.tasks import optimization_tasks
# Note: flag DB rows + cache 由 conftest._isolate_runtime_flags autouse fixture
# 在每条测试前后清空，无需在本文件再次处理。


# Tasks that still pass through to ``_stub`` (passive APIs not driven by Celery).
@pytest.mark.parametrize(
    "fn,kwargs,flag",
    [
        (optimization_tasks.run_hybrid_search_sync_once, {}, "memory.hybrid_search"),
        (optimization_tasks.run_rerank_warmup_once, {}, "memory.cross_encoder_rerank"),
        (
            optimization_tasks.run_precision_rewrite_once,
            {"chapter_num": 5},
            "repair.precision_rewrite",
        ),
        (
            optimization_tasks.run_context_embedding_score_once,
            {"chapter_num": 5},
            "memory.context_embedding_score",
        ),
    ],
)
def test_passive_stubs_flag_off(fn, kwargs, flag):
    out = fn(novel_id=1, **kwargs)
    assert out.get("skipped_disabled") is True
    assert out.get("flag") == flag


# Tasks now wired to real implementations: flag-off short-circuits, flag-on
# without required inputs short-circuits with ``skipped_no_*`` rather than
# touching the LLM.
@pytest.mark.parametrize(
    "fn,kwargs,flag",
    [
        (
            optimization_tasks.run_alias_build_once,
            {"chapter_num": None},
            "consistency.alias_registry_v1",
        ),
        (
            optimization_tasks.run_volume_brief_distill_once,
            {"volume_no": 1},
            "memory.volume_brief_distill",
        ),
        (
            optimization_tasks.run_spacetime_extract_once,
            {"chapter_num": 1},
            "consistency.spacetime_v1",
        ),
        (
            optimization_tasks.run_voice_drift_once,
            {"chapter_num": 1},
            "quality.voice_drift_audit",
        ),
        (
            optimization_tasks.run_foreshadow_lifecycle_once,
            {"chapter_num": 1},
            "consistency.foreshadow_lifecycle_v1",
        ),
        (
            optimization_tasks.run_outline_audit_once,
            {"chapter_num": 1},
            "quality.outline_promise_audit",
        ),
        (
            optimization_tasks.run_reader_lens_once,
            {"chapter_num": 1},
            "quality.reader_lens_audit",
        ),
    ],
)
def test_wired_runners_flag_off(fn, kwargs, flag):
    out = fn(novel_id=1, **kwargs)
    assert out.get("skipped_disabled") is True
    assert out.get("flag") == flag


def test_wired_runner_flag_on_without_input_returns_skipped():
    feature_flags.set_flag(
        "consistency.spacetime_v1",
        enabled=True,
        rollout_pct=100,
        changed_by="test",
        reason="enable",
    )
    out = optimization_tasks.run_spacetime_extract_once(novel_id=1, chapter_num=1)
    assert out.get("skipped_no_text") is True


def test_passive_stub_flag_on_emits_event():
    feature_flags.set_flag(
        "memory.hybrid_search",
        enabled=True,
        rollout_pct=100,
        changed_by="test",
        reason="enable",
    )
    out = optimization_tasks.run_hybrid_search_sync_once(novel_id=1)
    assert out.get("executed") is True
    assert out.get("stub") is True
