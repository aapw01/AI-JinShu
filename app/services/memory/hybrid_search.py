"""Hybrid search (#3b).

把字面量 BM25 与稠密 embedding 结果做 RRF（Reciprocal Rank Fusion）融合。
依赖：

- BM25：内置 ``rank_bm25`` 单文件实现（避免新增 PyPI 依赖）。
- Dense：复用 ``app.core.llm.get_embedding_model``，余弦相似度。
- 融合：``RRF(d) = Σ 1/(k+rank_i(d))``，``k=60``（业界默认）。

flag ``memory.hybrid_search`` 关闭时 ``hybrid_search`` 退化为 dense-only
（行为等同旧 ``vector_store.search``），不破坏现有调用方。
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

from app.core.feature_flags import is_enabled

logger = logging.getLogger(__name__)


_TOKEN_RE = re.compile(r"[\u4e00-\u9fa5]|[A-Za-z0-9]+")


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    return _TOKEN_RE.findall(text)


@dataclass
class Document:
    doc_id: str
    text: str
    embedding: list[float] | None = None


@dataclass
class ScoredDoc:
    doc_id: str
    score: float
    text: str


# --- BM25 -----------------------------------------------------------------
class BM25:
    """k1=1.5, b=0.75 默认参数。空 corpus 安全 fallback。"""

    def __init__(self, docs: Sequence[Document], *, k1: float = 1.5, b: float = 0.75):
        self.docs = list(docs)
        self.k1 = k1
        self.b = b
        self._token_lists: list[list[str]] = [_tokenize(d.text) for d in self.docs]
        self._doc_lens = [len(t) for t in self._token_lists]
        self._avgdl = (sum(self._doc_lens) / len(self._doc_lens)) if self._doc_lens else 0.0
        self._df: Counter[str] = Counter()
        for tokens in self._token_lists:
            for tok in set(tokens):
                self._df[tok] += 1
        self._n = len(self.docs)

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        if df == 0:
            return 0.0
        return math.log(1.0 + (self._n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 10) -> list[ScoredDoc]:
        if not self.docs:
            return []
        q_tokens = _tokenize(query)
        scores = [0.0] * self._n
        for term in q_tokens:
            idf = self._idf(term)
            if idf <= 0.0:
                continue
            for i, tokens in enumerate(self._token_lists):
                if not tokens:
                    continue
                tf = tokens.count(term)
                if tf == 0:
                    continue
                dl = self._doc_lens[i]
                denom = tf + self.k1 * (1 - self.b + self.b * dl / max(1.0, self._avgdl))
                scores[i] += idf * (tf * (self.k1 + 1)) / max(1e-9, denom)
        ranked = sorted(
            ((self.docs[i], s) for i, s in enumerate(scores) if s > 0),
            key=lambda x: -x[1],
        )[:top_k]
        return [ScoredDoc(doc_id=d.doc_id, score=s, text=d.text) for d, s in ranked]


# --- Dense ----------------------------------------------------------------
def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    if da == 0.0 or db == 0.0:
        return 0.0
    return num / (da * db)


def dense_search(
    query_vec: list[float],
    docs: Sequence[Document],
    *,
    top_k: int = 10,
) -> list[ScoredDoc]:
    if not query_vec or not docs:
        return []
    scored = []
    for d in docs:
        if not d.embedding:
            continue
        sim = _cosine(query_vec, d.embedding)
        if sim > 0:
            scored.append((d, sim))
    scored.sort(key=lambda x: -x[1])
    return [ScoredDoc(doc_id=d.doc_id, score=s, text=d.text) for d, s in scored[:top_k]]


# --- RRF fusion ----------------------------------------------------------
def rrf_fuse(
    *result_lists: Iterable[ScoredDoc],
    k: int = 60,
    top_k: int = 10,
) -> list[ScoredDoc]:
    """对多组检索结果做 Reciprocal Rank Fusion。"""
    fused: dict[str, float] = {}
    text_lookup: dict[str, str] = {}
    for results in result_lists:
        ranked = list(results)
        for rank, sd in enumerate(ranked):
            fused[sd.doc_id] = fused.get(sd.doc_id, 0.0) + 1.0 / (k + rank + 1)
            text_lookup[sd.doc_id] = sd.text
    ordered = sorted(fused.items(), key=lambda x: -x[1])[:top_k]
    return [ScoredDoc(doc_id=did, score=s, text=text_lookup.get(did, "")) for did, s in ordered]


# --- Public entry --------------------------------------------------------
def hybrid_search(
    query: str,
    *,
    docs: Sequence[Document],
    query_vec: list[float] | None = None,
    top_k: int = 10,
    novel_id: int | None = None,
) -> list[ScoredDoc]:
    """flag-on: BM25 + dense + RRF 融合。flag-off: dense-only（degrade）。"""
    if not docs:
        return []
    if not is_enabled("memory.hybrid_search", novel_id=novel_id):
        if query_vec is not None:
            return dense_search(query_vec, docs, top_k=top_k)
        return []
    bm25 = BM25(docs)
    bm25_hits = bm25.search(query, top_k=top_k * 2)
    dense_hits = dense_search(query_vec or [], docs, top_k=top_k * 2) if query_vec else []
    if bm25_hits and dense_hits:
        return rrf_fuse(bm25_hits, dense_hits, top_k=top_k)
    return (bm25_hits or dense_hits)[:top_k]


__all__ = [
    "BM25",
    "Document",
    "ScoredDoc",
    "dense_search",
    "hybrid_search",
    "rrf_fuse",
]
