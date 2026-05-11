"""Alias registry tests (#2)."""

from __future__ import annotations

import pytest

from app.core import feature_flags
from app.core.database import SessionLocal
from app.models.novel import AliasRegistry, Novel, NovelVersion
from app.services.memory.alias_registry import (
    AliasMatch,
    bulk_register,
    register_alias,
    resolve_aliases,
)


@pytest.fixture(autouse=True)
def _clear():
    feature_flags.invalidate_flags_cache()
    db = SessionLocal()
    try:
        db.query(AliasRegistry).delete()
        db.commit()
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(AliasRegistry).delete()
        db.commit()
    finally:
        db.close()


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _make_version(session) -> NovelVersion:
    n = Novel(title="t")
    session.add(n)
    session.commit()
    nv = NovelVersion(novel_id=n.id, version_no=1, status="draft")
    session.add(nv)
    session.commit()
    return nv


def test_register_idempotent(session):
    nv = _make_version(session)
    a = register_alias(
        session, novel_version_id=nv.id, character_key="K001", alias="李寻欢"
    )
    b = register_alias(
        session, novel_version_id=nv.id, character_key="K001", alias="李寻欢"
    )
    assert a is not None and b is not None
    assert a.id == b.id


def test_bulk_register(session):
    nv = _make_version(session)
    n = bulk_register(
        session,
        novel_version_id=nv.id,
        items=[
            {"character_key": "K001", "alias": "李寻欢", "priority": 10},
            {"character_key": "K001", "alias": "小李探花", "alias_type": "title"},
            {"character_key": "K001", "alias": ""},  # invalid, skipped
            {"character_key": "K002", "alias": "阿飞"},
        ],
    )
    assert n == 3


def test_resolve_aliases_flag_off_returns_empty(session):
    nv = _make_version(session)
    register_alias(session, novel_version_id=nv.id, character_key="K001", alias="李寻欢")
    out = resolve_aliases(
        "李寻欢说道", novel_version_id=nv.id, db=session
    )
    assert out == []


def test_resolve_aliases_longest_match(session):
    feature_flags.set_flag(
        "consistency.alias_registry_v1",
        enabled=True,
        rollout_pct=100,
        changed_by="test",
        reason="enable",
    )
    nv = _make_version(session)
    bulk_register(
        session,
        novel_version_id=nv.id,
        items=[
            {"character_key": "K001", "alias": "小李探花", "priority": 10, "alias_type": "title"},
            {"character_key": "K001", "alias": "小李"},
            {"character_key": "K002", "alias": "阿飞"},
        ],
    )
    text = "小李探花和阿飞同行，小李探花笑了。"
    matches = resolve_aliases(text, novel_version_id=nv.id, db=session)
    aliases_seen = [(m.alias, m.character_key) for m in matches]
    assert ("小李探花", "K001") in aliases_seen
    assert ("阿飞", "K002") in aliases_seen
    # 短的 "小李" 不应该作为独立匹配出现（被最长匹配吃掉）
    assert all(m.alias != "小李" for m in matches)


def test_resolve_aliases_position_order(session):
    feature_flags.set_flag(
        "consistency.alias_registry_v1",
        enabled=True,
        rollout_pct=100,
        changed_by="test",
        reason="enable",
    )
    nv = _make_version(session)
    bulk_register(
        session,
        novel_version_id=nv.id,
        items=[
            {"character_key": "A", "alias": "甲"},
            {"character_key": "B", "alias": "乙"},
        ],
    )
    text = "乙先来，然后甲到。"
    matches = resolve_aliases(text, novel_version_id=nv.id, db=session)
    assert [m.alias for m in matches] == ["乙", "甲"]


def test_invalid_inputs_return_empty(session):
    feature_flags.set_flag(
        "consistency.alias_registry_v1",
        enabled=True,
        rollout_pct=100,
        changed_by="test",
        reason="enable",
    )
    nv = _make_version(session)
    assert resolve_aliases("", novel_version_id=nv.id, db=session) == []
    assert resolve_aliases(None, novel_version_id=nv.id, db=session) == []
    register_alias(session, novel_version_id=nv.id, character_key="", alias="x") is None
