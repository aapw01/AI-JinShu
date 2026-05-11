"""Wave 3 memory tests (#3a / #3b / #3c)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.core import feature_flags
from app.core.database import SessionLocal
from app.models.novel import Novel, NovelVersion, StorySnapshot
from app.services.memory import volume_brief
from app.services.memory.cross_encoder_rerank import (
    rerank,
    register_rerank_runner,
)
from app.services.memory.hybrid_search import (
    BM25,
    Document,
    ScoredDoc,
    dense_search,
    hybrid_search,
    rrf_fuse,
)


@pytest.fixture(autouse=True)
def _clear():
    feature_flags.invalidate_flags_cache()
    db = SessionLocal()
    try:
        db.query(StorySnapshot).delete()
        db.commit()
    finally:
        db.close()
    register_rerank_runner(lambda _q, texts: [0.0] * len(texts))
    yield
    feature_flags.invalidate_flags_cache()


@pytest.fixture
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _enable(name: str):
    feature_flags.set_flag(
        name, enabled=True, rollout_pct=100, changed_by="t", reason="enable"
    )


# --- #3a ------------------------------------------------------------------
def test_volume_brief_flag_off_returns_none(session):
    n = Novel(title="t")
    session.add(n)
    session.commit()
    out = volume_brief.distill_volume_brief(
        session,
        novel_id=n.id,
        novel_version_id=None,
        volume_no=1,
        chapter_summaries=[{"chapter_num": 1, "summary": "x"}],
    )
    assert out is None


def test_volume_brief_persists(session, monkeypatch):
    n = Novel(title="t")
    session.add(n)
    session.commit()
    nv = NovelVersion(novel_id=n.id, version_no=1, status="draft")
    session.add(nv)
    session.commit()
    _enable("memory.volume_brief_distill")

    fake_resp = SimpleNamespace(content="第一卷概要：主角进入江湖。")
    monkeypatch.setattr(
        volume_brief, "get_llm", lambda: SimpleNamespace(invoke=lambda _p: fake_resp)
    )
    out = volume_brief.distill_volume_brief(
        session,
        novel_id=n.id,
        novel_version_id=nv.id,
        volume_no=1,
        chapter_summaries=[
            {"chapter_num": 1, "summary": "主角下山"},
            {"chapter_num": 2, "summary": "遇到师兄"},
            {"chapter_num": 3, "summary": "拜入门派"},
        ],
    )
    assert out is not None
    assert out.chapter_from == 1
    assert out.chapter_to == 3
    snap = session.query(StorySnapshot).filter_by(volume_no=1).one()
    assert snap.snapshot_json["volume_brief"]["text"].startswith("第一卷概要")


def test_volume_brief_empty_summaries_returns_none(session):
    n = Novel(title="t")
    session.add(n)
    session.commit()
    _enable("memory.volume_brief_distill")
    out = volume_brief.distill_volume_brief(
        session,
        novel_id=n.id,
        novel_version_id=None,
        volume_no=1,
        chapter_summaries=[],
    )
    assert out is None


def test_volume_brief_schema_v1_fallback_marks_event(session, monkeypatch):
    """LLM 输出无法解析成三段式 → schema_version=1 + agent_event 标记 fallback。"""
    from app.models.novel import AgentEvent

    session.query(AgentEvent).delete()
    session.commit()

    n = Novel(title="t")
    session.add(n)
    session.commit()
    nv = NovelVersion(novel_id=n.id, version_no=1, status="draft")
    session.add(nv)
    session.commit()
    _enable("memory.volume_brief_distill")

    # LLM 返回的不是 JSON 而是一段纯文本
    fake_resp = SimpleNamespace(content="主角终于走出新手村，从此踏上江湖路。")
    monkeypatch.setattr(
        volume_brief, "get_llm", lambda: SimpleNamespace(invoke=lambda _p: fake_resp)
    )

    out = volume_brief.distill_volume_brief(
        session,
        novel_id=n.id,
        novel_version_id=nv.id,
        volume_no=1,
        chapter_summaries=[{"chapter_num": 1, "summary": "x"}],
    )
    assert out is not None
    assert out.schema_version == 1
    assert out.characters == "" and out.conflicts == "" and out.foreshadowings == ""

    # 必须 emit schema_v1_fallback 事件
    fallback_events = (
        session.query(AgentEvent)
        .filter_by(
            agent_name="volume_brief_distiller", event_type="schema_v1_fallback"
        )
        .all()
    )
    assert len(fallback_events) == 1
    assert fallback_events[0].verdict == "warn"
    # 主 distill 事件也应该被标记 used_fallback=True
    main = (
        session.query(AgentEvent)
        .filter_by(agent_name="volume_brief_distiller", event_type="distill")
        .all()
    )
    assert main
    assert main[0].payload.get("used_fallback") is True
    assert main[0].payload.get("schema_version") == 1


# --- #3b ------------------------------------------------------------------
def _docs() -> list[Document]:
    return [
        Document(doc_id="d1", text="少年从山下的村庄出发，一路向北", embedding=[1.0, 0.0]),
        Document(doc_id="d2", text="老者在客栈讲述昔年江湖往事", embedding=[0.0, 1.0]),
        Document(doc_id="d3", text="少年遇到了师兄，结伴同行", embedding=[0.7, 0.7]),
    ]


def test_bm25_search_basic():
    docs = _docs()
    bm25 = BM25(docs)
    hits = bm25.search("少年", top_k=3)
    ids = [h.doc_id for h in hits]
    assert "d1" in ids and "d3" in ids


def test_bm25_empty_corpus():
    bm25 = BM25([])
    assert bm25.search("anything") == []


def test_dense_search_top():
    docs = _docs()
    out = dense_search([1.0, 0.0], docs, top_k=2)
    assert out[0].doc_id == "d1"


def test_rrf_fuse_combines():
    a = [ScoredDoc("d1", 1.0, "..."), ScoredDoc("d2", 0.5, "...")]
    b = [ScoredDoc("d2", 0.9, "..."), ScoredDoc("d3", 0.4, "...")]
    fused = rrf_fuse(a, b, top_k=3)
    ids = [f.doc_id for f in fused]
    assert "d2" in ids
    assert len(ids) == 3


def test_hybrid_flag_off_falls_back_to_dense():
    docs = _docs()
    out = hybrid_search("少年", docs=docs, query_vec=[1.0, 0.0], top_k=2)
    assert out[0].doc_id == "d1"


def test_hybrid_flag_on_uses_rrf():
    _enable("memory.hybrid_search")
    docs = _docs()
    out = hybrid_search("少年", docs=docs, query_vec=[1.0, 0.0], top_k=3)
    ids = {h.doc_id for h in out}
    assert "d1" in ids


# --- #3c ------------------------------------------------------------------
def test_rerank_flag_off_passthrough():
    docs = [ScoredDoc("d1", 0.1, "a"), ScoredDoc("d2", 0.9, "b")]
    out = rerank("q", docs, top_k=2)
    assert [d.doc_id for d in out] == ["d1", "d2"]


def test_rerank_flag_on_reorders():
    _enable("memory.cross_encoder_rerank")
    register_rerank_runner(lambda _q, texts: [0.1, 0.9][: len(texts)])
    docs = [ScoredDoc("d1", 0.1, "a"), ScoredDoc("d2", 0.9, "b")]
    out = rerank("q", docs, top_k=2)
    assert [d.doc_id for d in out] == ["d2", "d1"]


def test_rerank_runner_crash_safe():
    _enable("memory.cross_encoder_rerank")

    def _crash(_q, _texts):
        raise RuntimeError("fail")

    register_rerank_runner(_crash)
    docs = [ScoredDoc("d1", 0.1, "a"), ScoredDoc("d2", 0.9, "b")]
    out = rerank("q", docs, top_k=2)
    assert [d.doc_id for d in out] == ["d1", "d2"]


def test_rerank_invalid_score_length():
    _enable("memory.cross_encoder_rerank")
    register_rerank_runner(lambda _q, _texts: [0.5])  # wrong length
    docs = [ScoredDoc("d1", 0.1, "a"), ScoredDoc("d2", 0.9, "b")]
    out = rerank("q", docs, top_k=2)
    assert [d.doc_id for d in out] == ["d1", "d2"]
