"""Vector store wrapper using pgvector."""
import logging
import re
import time
from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.llm import embed_query
from app.core.logging_config import log_event
from app.core.database import SessionLocal
from app.models.novel import KnowledgeChunk

logger = logging.getLogger(__name__)


class VectorStoreWrapper:
    """Wrapper for pgvector - search knowledge chunks."""

    def search(
        self,
        novel_id: int,
        novel_version_id: int,
        query_text: Optional[str] = None,
        query_embedding: Optional[list[float]] = None,
        limit: int = 5,
        db: Optional[Session] = None,
    ) -> list[dict]:
        """Search relevant knowledge chunks for novel.

        ``flag memory.hybrid_search`` 开启时走 BM25 + dense + RRF 融合路径；
        失败任何环节自动降级到 dense-only。
        """
        # Hybrid search 路径（flag-controlled）
        hybrid_attempted = False
        try:
            from app.core.feature_flags import is_enabled

            if (
                query_text
                and is_enabled("memory.hybrid_search", novel_id=novel_id)
            ):
                hybrid_attempted = True
                hybrid_hits = self._search_hybrid(
                    novel_id=novel_id,
                    novel_version_id=novel_version_id,
                    query_text=query_text,
                    limit=limit,
                    db=db,
                )
                if hybrid_hits is not None:
                    try:
                        from app.core.metrics import context_selection_path_total

                        context_selection_path_total.inc(scoring="hybrid")
                    except Exception:
                        pass
                    return hybrid_hits
        except Exception:
            logger.debug("hybrid path init failed", exc_info=True)

        try:
            from app.core.metrics import context_selection_path_total

            context_selection_path_total.inc(
                scoring="dense_fallback" if hybrid_attempted else "dense"
            )
        except Exception:
            pass

        should_close = db is None
        db = db or SessionLocal()
        dense_started = time.perf_counter()
        try:
            stmt = select(KnowledgeChunk).where(
                KnowledgeChunk.novel_id == novel_id,
                KnowledgeChunk.novel_version_id == novel_version_id,
            )
            if query_embedding is None and query_text:
                query_embedding = embed_query(query_text)
            if query_embedding is not None:
                try:
                    stmt = stmt.order_by(
                        KnowledgeChunk.embedding.cosine_distance(query_embedding)
                    )
                except Exception as e:
                    log_event(
                        logger,
                        "vector.search.fallback",
                        level=logging.WARNING,
                        novel_id=novel_id,
                        error_class=type(e).__name__,
                        error_category="transient",
                    )
            rows = db.execute(stmt.limit(max(limit * 8, 20))).scalars().all()
            if query_embedding is None and query_text:
                rows = _lexical_rank(rows, query_text, limit)
            else:
                rows = rows[:limit]
            try:
                from app.core.metrics import memory_search_duration_ms

                memory_search_duration_ms.observe(
                    (time.perf_counter() - dense_started) * 1000.0,
                    path="dense_fallback" if hybrid_attempted else "dense",
                )
            except Exception:
                logger.debug("dense metric failed", exc_info=True)
            return [{"content": r.content, "chunk_type": r.chunk_type} for r in rows]
        finally:
            if should_close:
                db.close()

    def _search_hybrid(
        self,
        *,
        novel_id: int,
        novel_version_id: int,
        query_text: str,
        limit: int,
        db: Optional[Session],
    ) -> Optional[list[dict]]:
        """Hybrid 路径：拉候选 → ``hybrid_search`` 融合 → 返回 ``[{content, chunk_type}]``。

        关键约束：
        - **候选必须按 cosine 距离排序后再 limit**：否则 SQLite/PG 返回的物理顺序
          会让 BM25/RRF 退化成"对前 N 条随机 chunk 重排"，长篇小说下完全没意义。
        - 取不到 query_vec 时退化到 ``KnowledgeChunk.id`` 排序，保证确定性；同时
          打 ``context_selection_path_total{scoring="hybrid_no_dense"}`` 用于诊断。
        - 任何异常都返回 ``None``，让外层走 dense 兜底。
        """
        started = time.perf_counter()
        try:
            from app.services.memory.hybrid_search import Document, hybrid_search

            should_close = db is None
            session = db or SessionLocal()
            try:
                try:
                    query_vec = embed_query(query_text)
                except Exception:
                    query_vec = None

                candidate_limit = max(limit * 8, 32)
                base_stmt = (
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.novel_id == novel_id)
                    .where(KnowledgeChunk.novel_version_id == novel_version_id)
                )
                ordered_stmt = base_stmt
                if query_vec is not None:
                    try:
                        ordered_stmt = base_stmt.order_by(
                            KnowledgeChunk.embedding.cosine_distance(query_vec)
                        )
                    except Exception:
                        # pgvector 不可用 / SQLite 等情况：退化到 id 排序保证确定性
                        ordered_stmt = base_stmt.order_by(KnowledgeChunk.id.asc())
                else:
                    ordered_stmt = base_stmt.order_by(KnowledgeChunk.id.asc())
                rows = (
                    session.execute(ordered_stmt.limit(candidate_limit))
                    .scalars()
                    .all()
                )
                if not rows:
                    return []
                docs = [
                    Document(
                        doc_id=str(r.id),
                        text=r.content or "",
                        embedding=list(r.embedding) if r.embedding is not None else None,
                    )
                    for r in rows
                ]
                row_by_id = {str(r.id): r for r in rows}
                fused = hybrid_search(
                    query_text,
                    docs=docs,
                    query_vec=query_vec,
                    top_k=limit,
                    novel_id=novel_id,
                )
                results: list[dict] = []
                for hit in fused:
                    row = row_by_id.get(str(hit.doc_id))
                    if row is None:
                        continue
                    results.append(
                        {"content": row.content, "chunk_type": row.chunk_type}
                    )
                try:
                    from app.core.metrics import memory_search_duration_ms

                    duration_ms = (time.perf_counter() - started) * 1000.0
                    memory_search_duration_ms.observe(
                        duration_ms, path="hybrid"
                    )
                except Exception:
                    logger.debug("hybrid metric failed", exc_info=True)
                return results
            finally:
                if should_close:
                    session.close()
        except Exception:
            logger.debug("hybrid search failed", exc_info=True)
            try:
                from app.core.metrics import memory_search_timeout_total

                memory_search_timeout_total.inc(path="hybrid")
            except Exception:
                pass
            return None

    def add_chunk(
        self,
        novel_id: int,
        novel_version_id: int,
        content: str,
        chunk_type: Optional[str] = None,
        embedding: Optional[list[float]] = None,
        metadata: Optional[dict] = None,
        db: Optional[Session] = None,
    ) -> None:
        """Add a knowledge chunk to the vector store."""
        should_close = db is None
        db = db or SessionLocal()
        try:
            started = time.perf_counter()
            db.add(KnowledgeChunk(
                novel_id=novel_id,
                novel_version_id=novel_version_id,
                content=content,
                chunk_type=chunk_type,
                embedding=embedding if embedding is not None else embed_query(content[:2000]),
                metadata_=metadata or {},
            ))
            db.commit()
            log_event(
                logger,
                "vector.chunk.added",
                novel_id=novel_id,
                chunk_type=chunk_type,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        except Exception as e:
            log_event(
                logger,
                "vector.chunk.add.error",
                level=logging.ERROR,
                novel_id=novel_id,
                chunk_type=chunk_type,
                error_class=type(e).__name__,
                error_code="VECTOR_ADD_FAILED",
                error_category="transient",
            )
            db.rollback()
        finally:
            if should_close:
                db.close()


def _tokenize(text: str) -> set[str]:
    """把文本切成用于词法匹配的 token 集合。"""
    lowered = text.lower()
    words = set(re.findall(r"[a-z0-9_]{2,}", lowered))
    cjk = {ch for ch in lowered if "\u4e00" <= ch <= "\u9fff"}
    return words | cjk


def _lexical_rank(rows: list[KnowledgeChunk], query_text: str, limit: int) -> list[KnowledgeChunk]:
    """在缺少向量时用词法重叠对知识块做兜底排序。"""
    q_tokens = _tokenize(query_text)
    if not q_tokens:
        return rows[:limit]

    def score(item: KnowledgeChunk) -> tuple[int, int]:
        """用 token 重叠数和内容长度做一个轻量的词法排序分值。"""
        text_tokens = _tokenize(item.content or "")
        overlap = len(q_tokens & text_tokens)
        return overlap, len(item.content or "")

    ranked = sorted(rows, key=score, reverse=True)
    return ranked[:limit]
