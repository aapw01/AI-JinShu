"""Embedding-based context rescoring (#10).

flag ``memory.context_embedding_score`` 开启时，``select_context_candidates``
末尾会调 ``rescore_candidates_by_embedding``，把字面量交集分作为初筛、
embedding 相似度做 top-K 重排。flag 关闭则跳过（pass-through）。

实现选择：
- 失败安全：embedding 调用失败、维度不匹配、空文本一律回退到 ``_selector_score``。
- 不依赖向量库：直接用 ``get_embedding_model()`` 在线计算（结果只在单次调用
  内 in-memory 缓存）。生产环境后续 PR 可换成 pgvector 检索。
"""

from __future__ import annotations

import logging
import math
from typing import Any

from app.core.feature_flags import is_enabled

logger = logging.getLogger(__name__)


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    if da == 0.0 or db == 0.0:
        return 0.0
    return num / (da * db)


def _safe_embed_many(texts: list[str]) -> list[list[float]] | None:
    """安全调用 embedding；任何异常 → None（让上游回退）。"""
    cleaned = [t.strip() if isinstance(t, str) else "" for t in texts]
    if not any(cleaned):
        return None
    try:
        from app.core.llm import get_embedding_model

        model = get_embedding_model()
        # langchain OpenAIEmbeddings 暴露 embed_documents
        vectors = model.embed_documents(cleaned)
        if not isinstance(vectors, list) or len(vectors) != len(cleaned):
            return None
        return vectors
    except Exception:
        logger.debug("context_embedding: safe embed failed", exc_info=True)
        return None


def rescore_candidates_by_embedding(
    *,
    outline_text: str,
    candidates: list[dict[str, Any]],
    content_key: str = "content",
    novel_id: int | None = None,
) -> list[dict[str, Any]]:
    """Re-rank candidates by cosine similarity to ``outline_text``.

    flag-off 或失败 → 原样返回 ``candidates``。
    flag-on 且成功 → 按 ``embed_score = 0.7*cos + 0.3*norm(_selector_score)``
    重排（保留所有候选，只重新排序，不删项）。
    """
    if not is_enabled("memory.context_embedding_score", novel_id=novel_id):
        return candidates
    if not candidates:
        return candidates

    contents: list[str] = []
    for c in candidates:
        if not isinstance(c, dict):
            contents.append("")
            continue
        contents.append(
            str(
                c.get(content_key)
                or c.get("summary")
                or c.get("text")
                or c.get("line")
                or ""
            )
        )
    vectors = _safe_embed_many([outline_text] + contents)
    if vectors is None or len(vectors) != len(contents) + 1:
        return candidates

    outline_vec = vectors[0]
    cand_vecs = vectors[1:]
    sims = [_cosine(outline_vec, v) for v in cand_vecs]

    # 归一化字面量分到 0~1
    raw_scores = [
        float(c.get("_selector_score") or 0.0) if isinstance(c, dict) else 0.0
        for c in candidates
    ]
    max_raw = max(raw_scores) if raw_scores else 0.0
    norm_raw = [r / max_raw if max_raw > 0 else 0.0 for r in raw_scores]

    blended = [0.7 * s + 0.3 * n for s, n in zip(sims, norm_raw)]
    order = sorted(range(len(candidates)), key=lambda i: -blended[i])
    return [candidates[i] for i in order]


__all__ = ["rescore_candidates_by_embedding"]
