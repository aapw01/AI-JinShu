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
        """Search relevant knowledge chunks for novel."""
        should_close = db is None
        db = db or SessionLocal()
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
            return [{"content": r.content, "chunk_type": r.chunk_type} for r in rows]
        finally:
            if should_close:
                db.close()

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
