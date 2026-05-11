"""Cross-encoder rerank (#3c).

flag ``memory.cross_encoder_rerank`` 关闭时直接 pass-through（不重排）。开启
时调用注册的 ``rerank_runner``；运行时 runner 由 prompt-tuning PR 注入
（典型实现是 ONNX cross-encoder、AI Gateway rerank API 或 LLM-as-reranker）。

接口签名稳定：``rerank(query, docs, top_k)``。任何 runner 异常都退化回输入顺序。
"""

from __future__ import annotations

import logging
from typing import Callable, Sequence

from app.core.feature_flags import is_enabled
from app.services.memory.hybrid_search import ScoredDoc

logger = logging.getLogger(__name__)


# Runner 签名：(query, [text...]) → [score...]，长度需对齐输入。
RerankRunner = Callable[[str, list[str]], list[float]]
_runner: RerankRunner | None = None


def register_rerank_runner(runner: RerankRunner) -> None:
    global _runner
    _runner = runner


def _identity_runner(_query: str, texts: list[str]) -> list[float]:
    """flag 关闭或未注册 runner → 全部打 0 分（保持原顺序）。"""
    return [0.0] * len(texts)


def rerank(
    query: str,
    docs: Sequence[ScoredDoc],
    *,
    top_k: int = 10,
    novel_id: int | None = None,
) -> list[ScoredDoc]:
    """flag-controlled rerank。失败 / flag-off → 原样切片。"""
    if not docs:
        return []
    if not is_enabled("memory.cross_encoder_rerank", novel_id=novel_id):
        return list(docs)[:top_k]
    runner = _runner or _identity_runner
    try:
        scores = runner(query, [d.text for d in docs])
    except Exception:
        logger.exception("cross_encoder_rerank: runner crashed")
        return list(docs)[:top_k]
    if not isinstance(scores, list) or len(scores) != len(docs):
        logger.warning("cross_encoder_rerank: runner returned invalid scores")
        return list(docs)[:top_k]
    paired = list(zip(docs, scores))
    paired.sort(key=lambda x: -float(x[1] or 0.0))
    return [
        ScoredDoc(doc_id=d.doc_id, score=float(s or 0.0), text=d.text)
        for d, s in paired[:top_k]
    ]


__all__ = ["RerankRunner", "register_rerank_runner", "rerank"]
