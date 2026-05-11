"""Post-chapter audit hooks (§A) — verify dispatch behavior with all flags off / on."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.core import feature_flags
from app.core.database import SessionLocal
from app.models.novel import AgentEvent, Novel
from app.services.generation.post_chapter_hooks import (
    _outline_to_contract,
    _resolve_main_character,
    run_post_chapter_hooks,
)


@pytest.fixture(autouse=True)
def _clear():
    feature_flags.invalidate_flags_cache()
    db = SessionLocal()
    try:
        db.query(AgentEvent).delete()
        db.commit()
    finally:
        db.close()
    yield
    feature_flags.invalidate_flags_cache()


@pytest.fixture
def novel():
    db = SessionLocal()
    try:
        n = Novel(title="t")
        db.add(n)
        db.commit()
        db.refresh(n)
        yield n
    finally:
        db.close()


def test_resolve_main_character():
    # 只信显式主角字段。`characters[0]` 这种"本章登场列表第一项"启发式被禁用
    # （review/2026-05-11 指出会让 voice_drift 拿错画像）。
    assert _resolve_main_character({"main_character": "李白"}) == "李白"
    assert _resolve_main_character({"protagonist": "杜甫"}) == "杜甫"
    assert _resolve_main_character({"pov_character": "王维"}) == "王维"
    # dict 形式的主角字段也被接受
    assert _resolve_main_character({"main_character": {"name": "苏轼"}}) == "苏轼"
    # characters[0] 启发式被显式拒绝 —— 返回 None，让下游放弃当章 voice audit
    assert _resolve_main_character({"characters": ["a"]}) is None
    assert _resolve_main_character({"characters": [{"name": "x"}]}) is None
    assert _resolve_main_character({}) is None


def test_outline_to_contract():
    c = _outline_to_contract(
        {
            "chapter_objective": "解开身世之谜",
            "required_new_information": ["他亲生母亲是谁"],
            "payoff": "母子相认",
            "opening_scene": "破庙",
        },
        chapter_num=5,
    )
    assert c is not None
    assert c.chapter_num == 5
    assert c.chapter_objective == "解开身世之谜"
    assert "他亲生母亲是谁" in c.required_new_information
    assert c.payoff == "母子相认"


def test_outline_to_contract_skip_when_no_objective():
    assert _outline_to_contract({"key_information": []}, chapter_num=1) is None


def test_run_hooks_all_skipped_when_flags_off(novel):
    summary = run_post_chapter_hooks(
        novel_id=novel.id,
        novel_version_id=None,
        chapter_num=1,
        chapter_text="他走进破庙，发现一封信。",
        outline={"chapter_objective": "找信"},
    )
    assert summary["spacetime"] == "skip"
    assert summary["foreshadow_lifecycle"] == "skip"
    assert summary["voice_drift"] == "skip"
    assert summary["outline_audit"] == "skip"
    assert summary["reader_lens"] == "skip"


def test_run_hooks_dispatches_when_flags_on(novel):
    feature_flags.set_flag(
        "memory.spacetime_anchor_v1",
        enabled=True,
        rollout_pct=100,
        changed_by="t",
        reason="test",
    )
    feature_flags.set_flag(
        "consistency.foreshadow_lifecycle_v1",
        enabled=True,
        rollout_pct=100,
        changed_by="t",
        reason="test",
    )

    called: dict[str, int] = {"spacetime": 0, "foreshadow": 0}

    def _fake_st(db: Any, *, novel_id: int, **kw: Any) -> None:
        called["spacetime"] += 1
        return None

    def _fake_fs(db: Any, *, novel_id: int, **kw: Any) -> list[Any]:
        called["foreshadow"] += 1
        return []

    with patch(
        "app.services.agents.spacetime_extractor.extract_and_persist", _fake_st
    ), patch(
        "app.services.memory.foreshadow_lifecycle.advance_foreshadows", _fake_fs
    ):
        summary = run_post_chapter_hooks(
            novel_id=novel.id,
            novel_version_id=None,
            chapter_num=1,
            chapter_text="正文",
            outline={"chapter_objective": "obj"},
        )
    assert called["spacetime"] == 1
    assert called["foreshadow"] == 1
    assert summary["spacetime"] == "ok"
    assert summary["foreshadow_lifecycle"] == "ok"


def test_run_hooks_outline_audit_to_patch_writer_chain(novel):
    feature_flags.set_flag(
        "quality.outline_promise_audit",
        enabled=True,
        rollout_pct=100,
        changed_by="t",
        reason="test",
    )
    feature_flags.set_flag(
        "repair.precision_rewrite",
        enabled=True,
        rollout_pct=100,
        changed_by="t",
        reason="test",
    )

    from app.services.agents.contracts.outline import (
        OutlineAuditReport,
        OutlinePromiseVerdict,
    )
    from app.services.agents.contracts.patch import PatchResult

    chapter_text = "前文很多铺垫……" + "他走进破庙，找到了线索。" + "结尾"
    span_text = "他走进破庙，找到了线索。"
    s = chapter_text.index(span_text)
    e = s + len(span_text)

    fake_report = OutlineAuditReport(
        chapter_num=3,
        promises=[
            OutlinePromiseVerdict(
                key="solve_origin",
                fulfilled="no",
                evidence_span=(s, e),
                note="未揭示真正的母亲是谁",
            ),
        ],
        must_fix_count=1,
    )
    fake_patch = PatchResult(
        patched_text="他走进破庙，赫然发现母亲留下的信物。",
        length_delta=2,
        introduces_new_characters=[],
    )

    patch_calls: list[Any] = []

    def _fake_audit(db: Any, **kw: Any) -> Any:
        return fake_report

    def _fake_patch(**kw: Any) -> Any:
        patch_calls.append(kw)
        return fake_patch

    with patch(
        "app.services.agents.outline_auditor.audit_chapter_outline", _fake_audit
    ), patch("app.services.agents.patch_writer.apply_patch", _fake_patch):
        summary = run_post_chapter_hooks(
            novel_id=novel.id,
            novel_version_id=None,
            chapter_num=3,
            chapter_text=chapter_text,
            outline={
                "chapter_objective": "解开身世之谜",
                "required_new_information": ["母亲身份"],
            },
        )
    assert summary["outline_audit"] == "ok"
    assert summary["patch_writer"] == "ok"
    assert len(patch_calls) == 1
    assert patch_calls[0]["instruction"].span.original_text == span_text
