"""#10 embedding context rescoring tests."""

from __future__ import annotations

import pytest

from app.core import feature_flags
from app.services.memory import context_embedding
from app.services.memory.context_embedding import rescore_candidates_by_embedding


@pytest.fixture(autouse=True)
def _clear():
    feature_flags.invalidate_flags_cache()
    yield
    feature_flags.invalidate_flags_cache()


def test_flag_off_passthrough():
    out = rescore_candidates_by_embedding(
        outline_text="x",
        candidates=[{"id": 1, "content": "a"}, {"id": 2, "content": "b"}],
    )
    assert [c["id"] for c in out] == [1, 2]


def test_flag_on_safe_embed_failure_falls_back(monkeypatch):
    feature_flags.set_flag(
        "memory.context_embedding_score",
        enabled=True,
        rollout_pct=100,
        changed_by="test",
        reason="enable",
    )
    # patch embed to fail
    monkeypatch.setattr(context_embedding, "_safe_embed_many", lambda _texts: None)
    out = rescore_candidates_by_embedding(
        outline_text="x",
        candidates=[{"id": 1, "content": "a"}, {"id": 2, "content": "b"}],
    )
    assert [c["id"] for c in out] == [1, 2]


def test_flag_on_reorders_by_embedding(monkeypatch):
    feature_flags.set_flag(
        "memory.context_embedding_score",
        enabled=True,
        rollout_pct=100,
        changed_by="test",
        reason="enable",
    )

    # outline embed = [1,0]; first candidate vec = [0,1] (sim 0); second [1,0] (sim 1)
    def _fake_embed(texts):
        out = []
        for t in texts:
            if t == "outline":
                out.append([1.0, 0.0])
            elif t == "match":
                out.append([1.0, 0.0])
            else:
                out.append([0.0, 1.0])
        return out

    monkeypatch.setattr(context_embedding, "_safe_embed_many", _fake_embed)
    out = rescore_candidates_by_embedding(
        outline_text="outline",
        candidates=[
            {"id": 1, "content": "no_match", "_selector_score": 0},
            {"id": 2, "content": "match", "_selector_score": 0},
        ],
    )
    # cand 2 has cosine 1, should rank first
    assert out[0]["id"] == 2
    assert out[1]["id"] == 1


def test_empty_candidates_returns_unchanged(monkeypatch):
    feature_flags.set_flag(
        "memory.context_embedding_score",
        enabled=True,
        rollout_pct=100,
        changed_by="test",
        reason="enable",
    )
    out = rescore_candidates_by_embedding(outline_text="x", candidates=[])
    assert out == []
