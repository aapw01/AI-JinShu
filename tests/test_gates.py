"""Gate config loader tests (Phase 0 §4.4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import gates


@pytest.fixture(autouse=True)
def _clear_cache():
    gates.invalidate_gates_cache()
    yield
    gates.invalidate_gates_cache()


def test_consistency_gates_load():
    g = gates.get_gate("consistency", "hard_constraint")
    assert g.mode == "strict"
    assert g.max_outline_revise == 2
    assert g.downgrade_to == "save_blocked"


def test_unknown_category_returns_default():
    g = gates.get_gate("nonexistent", "anything")
    assert g.mode == "warn"


def test_unknown_gate_returns_default_within_known_category():
    g = gates.get_gate("consistency", "nonexistent_gate")
    assert g.mode == "warn"
    assert g.max_outline_revise == 0


def test_per_novel_override(tmp_path: Path, monkeypatch):
    cat = tmp_path / "consistency.yaml"
    cat.write_text(
        """schema_version: 1
gates:
  hard_constraint:
    mode: strict
    max_outline_revise: 1
    downgrade_to: save_blocked
overrides:
  per_novel:
    "42":
      hard_constraint:
        mode: strict
        max_outline_revise: 5
        downgrade_to: warn
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(gates, "_PRESETS_ROOT", tmp_path)
    gates.invalidate_gates_cache()

    base = gates.get_gate("consistency", "hard_constraint")
    overridden = gates.get_gate("consistency", "hard_constraint", novel_id=42)
    assert base.max_outline_revise == 1
    assert overridden.max_outline_revise == 5
    assert overridden.downgrade_to == "warn"


def test_corrupt_yaml_returns_default(tmp_path: Path, monkeypatch):
    cat = tmp_path / "consistency.yaml"
    cat.write_text("::: not valid", encoding="utf-8")
    monkeypatch.setattr(gates, "_PRESETS_ROOT", tmp_path)
    gates.invalidate_gates_cache()

    g = gates.get_gate("consistency", "hard_constraint")
    assert g.mode == "warn"
    assert g.max_outline_revise == 0


def test_list_gates_consistency():
    listed = gates.list_gates("consistency")
    assert "hard_constraint" in listed
    assert listed["hard_constraint"].mode == "strict"
