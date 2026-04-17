"""PostgreSQL-first retrieval service for long-form generation context."""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Optional

from sqlalchemy import desc, func, select, update
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.llm import embed_query
from app.core.logging_config import log_event
from app.models.novel import KnowledgeChunk

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,8}")
_RRF_K = 50
_TRGM_MIN_SCORE = 0.08
_VECTOR_WEIGHT = 2.0
_FTS_WEIGHT = 1.0
_TRGM_WEIGHT = 0.6
_SOURCE_IMPORTANCE = {
    "global_spec": 0.75,
    "chapter_summary": 0.62,
    "chapter_continuity": 0.95,
    "chapter_fact_delta": 0.9,
}


def _looks_like_generic_summary(source_type: str, summary: str, chapter_num: int | None) -> bool:
    """识别“第N章事实增量/连续性关键点”这类低信息量占位摘要。"""
    normalized = str(summary or "").strip()
    if not normalized:
        return True
    generic_prefixes = {
        "chapter_continuity": ("连续性关键点", "连续性摘要"),
        "chapter_fact_delta": ("事实增量",),
    }
    for suffix in generic_prefixes.get(str(source_type or ""), ()):
        if normalized == suffix:
            return True
        if chapter_num and normalized == f"第{int(chapter_num)}章{suffix}":
            return True
    return False


def _high_signal_summary(item: dict[str, Any]) -> str:
    """优先提取可读、可复述的检索摘要，而不是保留模板式标题。"""
    source_type = str(item.get("source_type") or "chapter_summary")
    chapter_num = item.get("chapter_num")
    summary = str(item.get("summary") or "").strip()
    content = str(item.get("content") or "").strip()

    if summary and not _looks_like_generic_summary(source_type, summary, chapter_num):
        return summary[:160]
    if not content:
        return summary[:160]

    lines = [line.strip("- ").strip() for line in content.splitlines() if line.strip()]
    if source_type == "chapter_fact_delta":
        facts: list[str] = []
        for line in lines:
            if "事实增量" in line:
                continue
            if ":" in line:
                label, value = line.split(":", 1)
                if label.strip() in {"事件", "事实"} and value.strip():
                    facts.append(value.strip())
                elif value.strip():
                    facts.append(line.strip())
            else:
                facts.append(line)
            if len(facts) >= 2:
                break
        if facts:
            return "；".join(facts)[:160]

    if source_type == "chapter_continuity":
        continuity_parts: list[str] = []
        for line in lines:
            if "连续性摘要" in line:
                continue
            if ":" in line:
                label, value = line.split(":", 1)
                value = value.strip()
                if value:
                    continuity_parts.append(f"{label.strip()}:{value}")
            elif line:
                continuity_parts.append(line)
            if len(continuity_parts) >= 3:
                break
        if continuity_parts:
            return "；".join(continuity_parts)[:160]

    for line in lines:
        if line:
            return line[:160]
    return summary[:160]


def _unique_preserve_order(values: Iterable[str]) -> list[str]:
    """对 token 列表做顺序去重，避免检索文本过长且重复。"""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        item = str(value or "").strip().lower()
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def tokenize_search_terms(*parts: Any) -> list[str]:
    """把中文/英文混合文本规整为适合检索的 token 列表。"""
    tokens: list[str] = []
    joined = "\n".join(str(part or "") for part in parts if str(part or "").strip())
    for token in _TOKEN_RE.findall(joined):
        stripped = token.strip()
        if not stripped:
            continue
        tokens.append(stripped)
        if re.fullmatch(r"[\u4e00-\u9fff]{3,8}", stripped):
            max_window = min(4, len(stripped))
            for window in range(2, max_window + 1):
                for index in range(0, len(stripped) - window + 1):
                    tokens.append(stripped[index:index + window])
    return _unique_preserve_order(tokens)


def build_search_text(*parts: Any) -> str:
    """生成供 PostgreSQL FTS / trigram 使用的规整检索文本。"""
    return " ".join(tokenize_search_terms(*parts))


def build_retrieved_memory_brief(items: list[dict[str, Any]]) -> str:
    """把检索结果压成适合注入 writer 的简短摘要。"""
    if not items:
        return ""
    grouped: dict[str, list[str]] = {
        "global_spec": [],
        "chapter_continuity": [],
        "chapter_fact_delta": [],
        "chapter_summary": [],
    }
    label_map = {
        "global_spec": "全局设定",
        "chapter_continuity": "连续性提醒",
        "chapter_fact_delta": "历史事实",
        "chapter_summary": "相关章节回顾",
    }
    for item in items[:4]:
        source_type = str(item.get("source_type") or "chapter_summary")
        chapter_num = item.get("chapter_num")
        summary = _high_signal_summary(item)
        if not summary:
            continue
        prefix = f"ch{int(chapter_num)}: " if isinstance(chapter_num, int) and chapter_num > 0 else ""
        grouped.setdefault(source_type, []).append(f"{prefix}{summary[:120]}")

    parts: list[str] = []
    for source_type in ("chapter_continuity", "chapter_fact_delta", "chapter_summary", "global_spec"):
        values = grouped.get(source_type) or []
        if not values:
            continue
        parts.append(f"{label_map.get(source_type, source_type)}: " + "；".join(values[:2]))
    return "\n".join(parts)


def build_retrieved_evidence(items: list[dict[str, Any]], *, limit: int = 3) -> list[dict[str, Any]]:
    """保留少量原始证据片段给 prompt 和调试使用。"""
    evidence: list[dict[str, Any]] = []
    for item in items[: max(limit * 2, limit)]:
        excerpt = str(item.get("content") or item.get("summary") or "").strip()
        if not excerpt:
            continue
        evidence.append(
            {
                "chunk_id": str(item.get("chunk_id") or item.get("id") or ""),
                "chapter_num": item.get("chapter_num"),
                "source_type": str(item.get("source_type") or ""),
                "summary": _high_signal_summary(item)[:160],
                "short_excerpt": excerpt[:180],
            }
        )
        if len(evidence) >= limit:
            break
    return evidence


def _rrf_score(rank: int, weight: float) -> float:
    """Reciprocal rank fusion score with a small smoothing constant."""
    return float(weight) / float(_RRF_K + max(rank, 1))


def _chapter_recency_bonus(chapter_num: int | None, current_chapter: int | None) -> float:
    """给更靠近当前章节的知识块一点轻量加分。"""
    if not chapter_num or not current_chapter:
        return 0.0
    distance = abs(int(current_chapter) - int(chapter_num))
    return max(0.0, 0.18 - (distance * 0.025))


class KnowledgeRetriever:
    """统一管理 knowledge chunk 的写入、检索和混合排序。"""

    def retrieve(
        self,
        *,
        novel_id: int,
        novel_version_id: int | None,
        query_text: str,
        current_chapter: int | None = None,
        limit: int = 5,
        db: Optional[Session] = None,
    ) -> list[dict[str, Any]]:
        """执行向量 + FTS + trigram 混合检索，并返回融合后的 chunk。"""
        should_close = db is None
        db = db or SessionLocal()
        try:
            dialect_name = str((db.bind.dialect.name if db.bind else "") or "")
            base_stmt = select(KnowledgeChunk).where(KnowledgeChunk.novel_id == novel_id)
            if novel_version_id is None:
                base_stmt = base_stmt.where(KnowledgeChunk.novel_version_id.is_(None))
            else:
                base_stmt = base_stmt.where(KnowledgeChunk.novel_version_id == novel_version_id)

            query_search_text = build_search_text(query_text)
            query_embedding = embed_query(query_text) if query_text.strip() else None
            fused: dict[int, dict[str, Any]] = {}

            def _ensure_row(row: KnowledgeChunk) -> dict[str, Any]:
                payload = fused.get(int(row.id))
                if payload is None:
                    payload = {
                        "chunk_id": str(row.id),
                        "id": int(row.id),
                        "source_type": str(row.source_type or row.chunk_type or ""),
                        "source_key": str(row.source_key or ""),
                        "chapter_num": int(row.chapter_num) if row.chapter_num else None,
                        "summary": str(row.summary or "")[:220],
                        "content": str(row.content or ""),
                        "importance_score": float(row.importance_score or 0.0),
                        "vector_rank": None,
                        "fts_rank": None,
                        "trigram_rank": None,
                        "vector_score": 0.0,
                        "fts_score": 0.0,
                        "trigram_score": 0.0,
                        "fusion_score": 0.0,
                    }
                    fused[int(row.id)] = payload
                return payload

            if query_embedding is not None:
                try:
                    vector_stmt = (
                        select(
                            KnowledgeChunk,
                            (1 - KnowledgeChunk.embedding.cosine_distance(query_embedding)).label("vector_score"),
                        )
                        .where(
                            KnowledgeChunk.novel_id == novel_id,
                            KnowledgeChunk.embedding.is_not(None),
                        )
                    )
                    if novel_version_id is None:
                        vector_stmt = vector_stmt.where(KnowledgeChunk.novel_version_id.is_(None))
                    else:
                        vector_stmt = vector_stmt.where(KnowledgeChunk.novel_version_id == novel_version_id)
                    vector_stmt = vector_stmt.order_by(
                        KnowledgeChunk.embedding.cosine_distance(query_embedding),
                        desc(KnowledgeChunk.importance_score),
                        desc(KnowledgeChunk.chapter_num),
                    ).limit(max(limit * 4, 12))
                    vector_rows = db.execute(vector_stmt).all()
                    for rank, (row, score) in enumerate(vector_rows, start=1):
                        payload = _ensure_row(row)
                        payload["vector_rank"] = rank
                        payload["vector_score"] = float(score or 0.0)
                        payload["fusion_score"] += _rrf_score(rank, _VECTOR_WEIGHT)
                except Exception as exc:
                    log_event(
                        logger,
                        "knowledge.retrieve.vector_fallback",
                        level=logging.WARNING,
                        novel_id=novel_id,
                        error_class=type(exc).__name__,
                        error_category="transient",
                    )

            if dialect_name == "postgresql" and query_search_text:
                ts_query = func.plainto_tsquery("simple", query_search_text)
                search_vector = func.coalesce(
                    KnowledgeChunk.search_vector,
                    func.to_tsvector("simple", func.coalesce(KnowledgeChunk.search_text, "")),
                )
                fts_rank = func.ts_rank_cd(search_vector, ts_query)
                fts_stmt = (
                    select(KnowledgeChunk, fts_rank.label("fts_score"))
                    .where(
                        KnowledgeChunk.novel_id == novel_id,
                        search_vector.op("@@")(ts_query),
                    )
                )
                if novel_version_id is None:
                    fts_stmt = fts_stmt.where(KnowledgeChunk.novel_version_id.is_(None))
                else:
                    fts_stmt = fts_stmt.where(KnowledgeChunk.novel_version_id == novel_version_id)
                fts_stmt = fts_stmt.order_by(
                    desc("fts_score"),
                    desc(KnowledgeChunk.importance_score),
                    desc(KnowledgeChunk.chapter_num),
                ).limit(max(limit * 4, 12))
                for rank, (row, score) in enumerate(db.execute(fts_stmt).all(), start=1):
                    payload = _ensure_row(row)
                    payload["fts_rank"] = rank
                    payload["fts_score"] = float(score or 0.0)
                    payload["fusion_score"] += _rrf_score(rank, _FTS_WEIGHT)

                trigram_score = func.similarity(KnowledgeChunk.search_text, query_search_text)
                trigram_stmt = (
                    select(KnowledgeChunk, trigram_score.label("trigram_score"))
                    .where(
                        KnowledgeChunk.novel_id == novel_id,
                        trigram_score >= _TRGM_MIN_SCORE,
                    )
                )
                if novel_version_id is None:
                    trigram_stmt = trigram_stmt.where(KnowledgeChunk.novel_version_id.is_(None))
                else:
                    trigram_stmt = trigram_stmt.where(KnowledgeChunk.novel_version_id == novel_version_id)
                trigram_stmt = trigram_stmt.order_by(
                    desc("trigram_score"),
                    desc(KnowledgeChunk.importance_score),
                    desc(KnowledgeChunk.chapter_num),
                ).limit(max(limit * 4, 12))
                for rank, (row, score) in enumerate(db.execute(trigram_stmt).all(), start=1):
                    payload = _ensure_row(row)
                    payload["trigram_rank"] = rank
                    payload["trigram_score"] = float(score or 0.0)
                    payload["fusion_score"] += _rrf_score(rank, _TRGM_WEIGHT)
            elif query_search_text:
                rows = db.execute(base_stmt.limit(max(limit * 8, 20))).scalars().all()
                query_terms = set(tokenize_search_terms(query_search_text))
                ranked = sorted(
                    rows,
                    key=lambda row: (
                        len(query_terms.intersection(set(tokenize_search_terms(row.search_text or row.content or "")))),
                        float(row.importance_score or 0.0),
                        int(row.chapter_num or 0),
                    ),
                    reverse=True,
                )[: max(limit * 4, 12)]
                for rank, row in enumerate(ranked, start=1):
                    payload = _ensure_row(row)
                    payload["fts_rank"] = rank
                    payload["fts_score"] = float(len(query_terms.intersection(set(tokenize_search_terms(row.search_text or row.content or "")))))
                    payload["fusion_score"] += _rrf_score(rank, _FTS_WEIGHT)

            for payload in fused.values():
                payload["fusion_score"] += float(payload.get("importance_score") or 0.0) * 0.1
                payload["fusion_score"] += _chapter_recency_bonus(payload.get("chapter_num"), current_chapter)

            return sorted(
                fused.values(),
                key=lambda item: (
                    float(item.get("fusion_score") or 0.0),
                    float(item.get("importance_score") or 0.0),
                    int(item.get("chapter_num") or 0),
                ),
                reverse=True,
            )[:limit]
        finally:
            if should_close:
                db.close()

    def upsert_chunk(
        self,
        *,
        novel_id: int,
        novel_version_id: int | None,
        source_type: str,
        source_key: str,
        content: str,
        summary: str = "",
        chapter_num: int | None = None,
        importance_score: float | None = None,
        metadata: dict[str, Any] | None = None,
        db: Optional[Session] = None,
    ) -> KnowledgeChunk:
        """按 `(novel_id, novel_version_id?, source_key)` 幂等写入一条知识块。"""
        should_close = db is None
        db = db or SessionLocal()
        try:
            stmt = select(KnowledgeChunk).where(
                KnowledgeChunk.novel_id == novel_id,
                KnowledgeChunk.source_key == source_key,
            )
            if novel_version_id is None:
                stmt = stmt.where(KnowledgeChunk.novel_version_id.is_(None))
            else:
                stmt = stmt.where(KnowledgeChunk.novel_version_id == novel_version_id)
            row = db.execute(stmt).scalar_one_or_none()
            normalized_summary = str(summary or content or "").strip()[:220]
            normalized_content = str(content or "").strip()
            normalized_search_text = build_search_text(source_type, source_key, normalized_summary, normalized_content)
            resolved_importance = float(
                importance_score
                if importance_score is not None
                else _SOURCE_IMPORTANCE.get(str(source_type or "").strip(), 0.6)
            )
            payload = {
                "source_type": str(source_type),
                "source_key": str(source_key),
                "chapter_num": chapter_num,
                "summary": normalized_summary,
                "content": normalized_content,
                "search_text": normalized_search_text,
                "importance_score": resolved_importance,
                "chunk_type": str(source_type),
                "metadata_": metadata or {},
            }
            if row is None:
                row = KnowledgeChunk(
                    novel_id=novel_id,
                    novel_version_id=novel_version_id,
                    embedding=embed_query(f"{normalized_summary}\n{normalized_content}"[:2000]) if normalized_content else None,
                    **payload,
                )
                db.add(row)
                db.flush()
            else:
                for key, value in payload.items():
                    setattr(row, key, value)
                row.embedding = embed_query(f"{normalized_summary}\n{normalized_content}"[:2000]) if normalized_content else None
                db.flush()

            dialect_name = str((db.bind.dialect.name if db.bind else "") or "")
            if dialect_name == "postgresql":
                db.execute(
                    update(KnowledgeChunk)
                    .where(KnowledgeChunk.id == row.id)
                    .values(search_vector=func.to_tsvector("simple", normalized_search_text))
                )
            else:
                row.search_vector = normalized_search_text
            if should_close:
                db.commit()
            else:
                db.flush()
            return row
        except Exception:
            if should_close:
                db.rollback()
            raise
        finally:
            if should_close:
                db.close()

    def upsert_global_spec_chunks_from_prewrite(
        self,
        *,
        novel_id: int,
        novel_version_id: int | None,
        prewrite: dict[str, Any] | None,
        db: Optional[Session] = None,
    ) -> None:
        """把 prewrite 中的全局设定整理成稳定的检索 chunk。"""
        spec = dict((prewrite or {}).get("specification") or (prewrite or {}).get("spec") or {})
        if not spec:
            return
        characters = spec.get("characters") or []
        world_rules = spec.get("world_rules") or spec.get("rules") or spec.get("world") or []
        plotlines = spec.get("plotlines") or spec.get("plot_lines") or []

        def _normalize_block(title: str, value: Any) -> str:
            if isinstance(value, list):
                rendered = []
                for item in value[:20]:
                    if isinstance(item, dict):
                        parts = [str(item.get(key) or "") for key in ("name", "role", "description", "goal", "summary")]
                        rendered.append(" / ".join(part for part in parts if part))
                    else:
                        rendered.append(str(item))
                body = "\n".join(f"- {line}" for line in rendered if str(line).strip())
            elif isinstance(value, dict):
                body = "\n".join(f"- {key}: {value[key]}" for key in value if str(value[key]).strip())
            else:
                body = str(value or "").strip()
            return f"{title}\n{body}".strip()

        blocks = [
            ("characters", "角色设定", characters),
            ("world", "世界观/规则", world_rules),
            ("plotlines", "主线/剧情线", plotlines),
        ]
        for section, title, value in blocks:
            content = _normalize_block(title, value)
            if not content.strip():
                continue
            self.upsert_chunk(
                novel_id=novel_id,
                novel_version_id=novel_version_id,
                source_type="global_spec",
                source_key=f"global_spec:{section}",
                summary=title,
                content=content,
                chapter_num=None,
                importance_score=0.82,
                metadata={"section": section},
                db=db,
            )
