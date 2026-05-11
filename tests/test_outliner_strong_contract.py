"""B：outliner 输出 character_aliases / spacetime_hint 强约束。"""

from __future__ import annotations

import pytest

from app.core import feature_flags
from app.core.database import SessionLocal
from app.models.novel import AgentEvent, AliasRegistry, Novel, NovelVersion
from app.services.memory.progression_state import (
    _normalize_character_aliases,
    _normalize_spacetime_hint,
    normalize_outline_contract,
)


@pytest.fixture(autouse=True)
def _clear():
    feature_flags.invalidate_flags_cache()
    db = SessionLocal()
    try:
        db.query(AgentEvent).delete()
        db.query(AliasRegistry).delete()
        db.commit()
    finally:
        db.close()
    yield
    feature_flags.invalidate_flags_cache()


def test_normalize_character_aliases_dict_form():
    out = _normalize_character_aliases({"李白": ["谪仙人", "李太白"]})
    assert out == [{"canonical": "李白", "aliases": ["谪仙人", "李太白"]}]


def test_normalize_character_aliases_list_form():
    out = _normalize_character_aliases(
        [{"canonical": "李白", "aliases": ["谪仙人"]}, {"canonical": "", "aliases": []}]
    )
    assert out == [{"canonical": "李白", "aliases": ["谪仙人"]}]


def test_normalize_character_aliases_garbage_input():
    assert _normalize_character_aliases(None) == []
    assert _normalize_character_aliases(["a", "b"]) == []


def test_normalize_spacetime_hint_complete():
    out = _normalize_spacetime_hint(
        {"place": "破庙", "time_of_day": "戌时", "weather": "雨", "prev_anchor": "客栈·黄昏"}
    )
    assert out["place"] == "破庙"
    assert out["prev_anchor"] == "客栈·黄昏"
    assert out["weather"] == "雨"


def test_normalize_spacetime_hint_missing_keys():
    out = _normalize_spacetime_hint({})
    assert set(out.keys()) == {"place", "time_of_day", "weather", "prev_anchor"}
    assert all(v == "" for v in out.values())


def test_normalize_outline_contract_carries_new_fields():
    out = normalize_outline_contract(
        {
            "title": "破庙夜",
            "purpose": "找信",
            "character_aliases": {"李白": ["谪仙人"]},
            "spacetime_hint": {"place": "破庙", "prev_anchor": "客栈·黄昏"},
        },
        chapter_num=3,
    )
    assert out["character_aliases"] == [
        {"canonical": "李白", "aliases": ["谪仙人"]}
    ]
    assert out["spacetime_hint"]["place"] == "破庙"
    assert out["spacetime_hint"]["prev_anchor"] == "客栈·黄昏"


def test_post_chapter_hook_registers_aliases_via_outline():
    feature_flags.set_flag(
        "consistency.alias_registry_v1",
        enabled=True,
        rollout_pct=100,
        changed_by="t",
        reason="enable",
    )
    db = SessionLocal()
    try:
        n = Novel(title="t")
        db.add(n)
        db.commit()
        nv = NovelVersion(novel_id=n.id, version_no=1, status="draft")
        db.add(nv)
        db.commit()
        from app.services.generation.post_chapter_hooks import run_post_chapter_hooks

        run_post_chapter_hooks(
            novel_id=n.id,
            novel_version_id=nv.id,
            chapter_num=1,
            chapter_text="正文",
            outline={
                "chapter_objective": "obj",
                "character_aliases": [
                    {"canonical": "李白", "aliases": ["谪仙人", "李太白"]},
                ],
            },
        )
        rows = db.query(AliasRegistry).all()
        aliases = sorted(r.alias for r in rows)
        assert "李白" in aliases
        assert "谪仙人" in aliases
        assert "李太白" in aliases
    finally:
        db.close()
