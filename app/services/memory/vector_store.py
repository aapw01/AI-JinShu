"""Compatibility wrapper around the newer knowledge retriever service."""
from typing import Optional
from sqlalchemy.orm import Session

from app.services.memory.retriever import KnowledgeRetriever


class VectorStoreWrapper:
    """兼容旧调用点的薄包装，内部委托给 `KnowledgeRetriever`。"""

    def __init__(self) -> None:
        self._retriever = KnowledgeRetriever()

    def search(
        self,
        novel_id: int,
        novel_version_id: int,
        query_text: Optional[str] = None,
        query_embedding: Optional[list[float]] = None,
        limit: int = 5,
        db: Optional[Session] = None,
    ) -> list[dict]:
        """兼容旧接口，返回的字段比历史版本更丰富。"""
        del query_embedding
        return self._retriever.retrieve(
            novel_id=novel_id,
            novel_version_id=novel_version_id,
            query_text=str(query_text or ""),
            limit=limit,
            db=db,
        )

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
        """兼容旧接口，为缺少 source 元数据的调用生成默认键。"""
        del embedding
        source_type = str(chunk_type or "chapter_summary")
        source_key = f"legacy:{source_type}:{abs(hash((novel_id, novel_version_id, content[:120]))) % 10_000_000}"
        self._retriever.upsert_chunk(
            novel_id=novel_id,
            novel_version_id=novel_version_id,
            source_type=source_type,
            source_key=source_key,
            summary=str(content or "")[:180],
            content=content,
            chapter_num=(metadata or {}).get("chapter_num") if isinstance(metadata, dict) else None,
            metadata=metadata or {},
            db=db,
        )
