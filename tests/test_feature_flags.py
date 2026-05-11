"""Tests for app/core/feature_flags.py + app/tasks/feature_flags.py (Phase 0 §4.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.core import feature_flags
from app.core.database import SessionLocal
from app.core.feature_flags import (
    apply_flag_state_from_yaml,
    get_flag_state,
    invalidate_flags_cache,
    is_enabled,
    set_flag,
)
from app.models.novel import FlagAuditLog, SystemRuntimeSetting
from app.tasks.feature_flags import sync_flags_from_yaml


@pytest.fixture(autouse=True)
def _reset_flag_state():
    """每个测试前清掉 flag.* 副本和 audit 表，避免互相污染。"""
    invalidate_flags_cache()
    db = SessionLocal()
    try:
        db.query(FlagAuditLog).delete(synchronize_session=False)
        db.query(SystemRuntimeSetting).filter(
            SystemRuntimeSetting.setting_key.like("flag.%")
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()
    yield
    invalidate_flags_cache()


def _audit_count(flag_name: str) -> int:
    db = SessionLocal()
    try:
        rows = (
            db.execute(
                select(FlagAuditLog).where(FlagAuditLog.flag_name == flag_name)
            )
            .scalars()
            .all()
        )
        return len(rows)
    finally:
        db.close()


# ------------------------ is_enabled --------------------------------------


def test_unknown_flag_returns_false_by_default() -> None:
    assert is_enabled("nonexistent.flag") is False
    assert is_enabled("nonexistent.flag", novel_id=42) is False


def test_set_flag_then_is_enabled_global() -> None:
    set_flag(
        "consistency.blocker_hard_gate",
        enabled=True,
        changed_by="test:set-flag",
        reason="enable for global rollout test",
    )
    assert is_enabled("consistency.blocker_hard_gate") is True
    assert is_enabled("consistency.blocker_hard_gate", novel_id=1) is True


def test_allowlist_short_circuits_even_when_global_disabled() -> None:
    set_flag(
        "consistency.blocker_hard_gate",
        enabled=False,
        rollout_pct=0,
        novel_allowlist=[123],
        changed_by="test:allowlist",
        reason="enable allowlist for novel 123",
    )
    assert is_enabled("consistency.blocker_hard_gate") is False
    assert is_enabled("consistency.blocker_hard_gate", novel_id=123) is True
    assert is_enabled("consistency.blocker_hard_gate", novel_id=999) is False


def test_rollout_pct_hash_bucket_is_deterministic() -> None:
    set_flag(
        "consistency.blocker_hard_gate",
        enabled=False,
        rollout_pct=100,
        changed_by="test:rollout",
        reason="full rollout via rollout_pct",
    )
    assert is_enabled("consistency.blocker_hard_gate", novel_id=1) is True
    assert is_enabled("consistency.blocker_hard_gate", novel_id=12345) is True
    assert is_enabled("consistency.blocker_hard_gate") is False


def test_rollout_pct_partial_distributes_buckets() -> None:
    set_flag(
        "consistency.blocker_hard_gate",
        enabled=False,
        rollout_pct=50,
        changed_by="test:partial",
        reason="50% rollout",
    )
    enabled_ids = sum(
        1
        for nid in range(2000)
        if is_enabled("consistency.blocker_hard_gate", novel_id=nid)
    )
    assert 800 <= enabled_ids <= 1200


def test_db_failure_returns_false_fail_close(monkeypatch) -> None:
    set_flag(
        "consistency.blocker_hard_gate",
        enabled=True,
        changed_by="test:fail-close",
        reason="setup before failure",
    )
    invalidate_flags_cache()

    def _boom():
        raise RuntimeError("simulated db outage")

    monkeypatch.setattr(feature_flags, "SessionLocal", _boom)
    assert is_enabled("consistency.blocker_hard_gate") is False
    assert is_enabled("consistency.blocker_hard_gate", novel_id=1) is False


# ------------------------ set_flag --------------------------------------


def test_set_flag_writes_audit_log_with_before_after() -> None:
    set_flag(
        "consistency.blocker_hard_gate",
        enabled=True,
        rollout_pct=10,
        changed_by="user:alice@test.local",
        reason="canary 10%",
    )
    set_flag(
        "consistency.blocker_hard_gate",
        rollout_pct=50,
        changed_by="cv_watchdog",
        reason="auto promote to 50",
    )

    db = SessionLocal()
    try:
        rows = (
            db.execute(
                select(FlagAuditLog)
                .where(FlagAuditLog.flag_name == "consistency.blocker_hard_gate")
                .order_by(FlagAuditLog.id.asc())
            )
            .scalars()
            .all()
        )
    finally:
        db.close()

    assert len(rows) == 2
    first, second = rows
    assert first.before_state == {}
    assert first.after_state["enabled"] is True
    assert first.after_state["rollout_pct"] == 10
    assert second.before_state["rollout_pct"] == 10
    assert second.after_state["rollout_pct"] == 50
    assert first.changed_by == "user:alice@test.local"
    assert second.changed_by == "cv_watchdog"


def test_set_flag_invalidates_cache_immediately() -> None:
    assert is_enabled("consistency.blocker_hard_gate") is False
    set_flag(
        "consistency.blocker_hard_gate",
        enabled=True,
        changed_by="test:cache",
        reason="enable",
    )
    assert is_enabled("consistency.blocker_hard_gate") is True


def test_set_flag_validates_changed_by_and_reason() -> None:
    with pytest.raises(ValueError, match="reason is required"):
        set_flag(
            "consistency.blocker_hard_gate",
            enabled=True,
            changed_by="test:x",
            reason="",
        )
    with pytest.raises(ValueError, match="changed_by is required"):
        set_flag(
            "consistency.blocker_hard_gate",
            enabled=True,
            changed_by="",
            reason="ok",
        )


def test_set_flag_rejects_out_of_range_pct_and_bad_allowlist() -> None:
    with pytest.raises(ValueError, match="rollout_pct"):
        set_flag(
            "consistency.blocker_hard_gate",
            rollout_pct=150,
            changed_by="test:bad",
            reason="bad pct",
        )
    with pytest.raises(ValueError, match="novel_allowlist"):
        set_flag(
            "consistency.blocker_hard_gate",
            novel_allowlist=["abc"],  # type: ignore[list-item]
            changed_by="test:bad",
            reason="bad list",
        )


def test_set_flag_extra_cannot_override_controlled_field() -> None:
    with pytest.raises(ValueError, match="controlled field"):
        set_flag(
            "consistency.blocker_hard_gate",
            extra={"enabled": True},
            changed_by="test:bad",
            reason="extra-clash",
        )


def test_get_flag_state_returns_default_filled_payload() -> None:
    set_flag(
        "consistency.blocker_hard_gate",
        enabled=True,
        changed_by="test:state",
        reason="enable",
    )
    state = get_flag_state("consistency.blocker_hard_gate")
    assert state is not None
    assert state["enabled"] is True
    assert state["rollout_pct"] == 0
    assert state["novel_allowlist"] == []
    assert "owner" in state


# ------------------------ yaml sync --------------------------------------


def test_apply_flag_state_from_yaml_skips_when_unchanged() -> None:
    state = {"enabled": False, "rollout_pct": 0, "owner": "team:test"}
    first = apply_flag_state_from_yaml("consistency.blocker_hard_gate", state)
    second = apply_flag_state_from_yaml("consistency.blocker_hard_gate", state)
    assert first is not None
    assert second is None
    assert _audit_count("consistency.blocker_hard_gate") == 1


def test_sync_flags_from_yaml_loads_registry_and_writes_db(tmp_path: Path) -> None:
    flags_dir = tmp_path / "flags"
    flags_dir.mkdir()
    (flags_dir / "registry.yaml").write_text(
        """
schema_version: 1
flags:
  test.alpha:
    enabled: true
    rollout_pct: 20
    owner: team:alpha
  test.beta:
    enabled: false
    owner: team:beta
""".strip(),
        encoding="utf-8",
    )

    summary = sync_flags_from_yaml(flags_dir)
    assert summary == {"test.alpha": "synced", "test.beta": "synced"}

    invalidate_flags_cache()
    assert is_enabled("test.alpha") is True
    assert is_enabled("test.beta") is False

    summary_again = sync_flags_from_yaml(flags_dir)
    assert summary_again == {"test.alpha": "skipped", "test.beta": "skipped"}


def test_sync_flags_single_file_overrides_registry(tmp_path: Path) -> None:
    flags_dir = tmp_path / "flags"
    flags_dir.mkdir()
    (flags_dir / "registry.yaml").write_text(
        """
flags:
  test.gamma:
    enabled: false
    owner: team:gamma
""".strip(),
        encoding="utf-8",
    )
    (flags_dir / "test.gamma.yaml").write_text(
        """
enabled: true
rollout_pct: 5
owner: team:gamma-override
""".strip(),
        encoding="utf-8",
    )

    sync_flags_from_yaml(flags_dir)
    invalidate_flags_cache()
    state = get_flag_state("test.gamma")
    assert state is not None
    assert state["enabled"] is True
    assert state["rollout_pct"] == 5
    assert state["owner"] == "team:gamma-override"


def test_repository_registry_yaml_has_all_14_flags() -> None:
    """Real registry.yaml must list every flag named in §4.2."""
    import yaml

    repo_root = Path(__file__).resolve().parents[1]
    registry_path = repo_root / "presets" / "flags" / "registry.yaml"
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    flags = data.get("flags") or {}
    expected = {
        "consistency.blocker_hard_gate",
        "consistency.alias_registry_v1",
        "memory.volume_brief_distill",
        "memory.hybrid_search",
        "memory.cross_encoder_rerank",
        "consistency.spacetime_v1",
        "quality.voice_drift_audit",
        "consistency.foreshadow_lifecycle_v1",
        "quality.outline_promise_audit",
        "repair.precision_rewrite",
        "memory.fact_arbitration_v1",
        "memory.context_embedding_score",
        "extractor.self_heal",
        "quality.reader_lens_audit",
        "cost.budget_enforcement",
    }
    assert set(flags.keys()) == expected
    for name, state in flags.items():
        assert state.get("enabled") is False, f"{name} must default to disabled"
        assert state.get("rollout_pct", 0) == 0, f"{name} must default to rollout_pct=0"
