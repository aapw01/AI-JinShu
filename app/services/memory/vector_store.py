"""Vector store wrapper using pgvector."""
import logging
from typing import Optional
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.novel import KnowledgeChunk

logger = logging.getLogger(__name__)


class VectorStoreWrapper:
    """Wrapper for pgvector - search knowledge chunks."""

    def search(
        self,
        novel_id: int,
        query_embedding: Optional[list[float]] = None,
        limit: int = 5,
        db: Optional[Session] = None,
    ) -> list[dict]:
        """Search relevant knowledge chunks for novel."""
        should_close = db is None
        db = db or SessionLocal()
        try:
            query = db.query(KnowledgeChunk).filter(KnowledgeChunk.novel_id == novel_id)
            if query_embedding is not None:
                try:
                    query = query.order_by(
                        KnowledgeChunk.embedding.cosine_distance(query_embedding)
                    )
                except Exception as e:
                    logger.warning(f"Vector search failed: {e}")
            rows = query.limit(limit).all()
            return [{"content": r.content, "chunk_type": r.chunk_type} for r in rows]
        finally:
            if should_close:
                db.close()

    def add_chunk(
        self,
        novel_id: int,
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
            db.add(KnowledgeChunk(
                novel_id=novel_id,
                content=content,
                chunk_type=chunk_type,
                embedding=embedding,
                metadata_=metadata or {},
            ))
            db.commit()
        except Exception as e:
            logger.error(f"Failed to add knowledge chunk: {e}")
            db.rollback()
        finally:
            if should_close:
                db.close()
