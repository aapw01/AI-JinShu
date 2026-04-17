from __future__ import annotations

from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from app.models.novel import KnowledgeChunk
from app.services.memory.retriever import build_retrieved_memory_brief


def test_build_retrieved_memory_brief_prefers_high_signal_fact_content():
    brief = build_retrieved_memory_brief(
        [
            {
                "source_type": "chapter_fact_delta",
                "chapter_num": 3,
                "summary": "第3章事实增量",
                "content": "第3章事实增量\n- 事件: 林舟确认幕后主使\n- 事实: 云家 / truth / 真正操盘者浮出水面",
            },
            {
                "source_type": "chapter_continuity",
                "chapter_num": 3,
                "summary": "第3章连续性关键点",
                "content": "第3章连续性摘要\n章节目标: 确认幕后主使\n关系变化: 林舟对苏晚的信任下降",
            },
        ]
    )

    assert "林舟确认幕后主使" in brief
    assert "林舟对苏晚的信任下降" in brief
    assert "第3章事实增量" not in brief
    assert "第3章连续性关键点" not in brief


def test_knowledge_chunk_unique_index_handles_null_version_scope():
    index = next(
        item
        for item in KnowledgeChunk.__table__.indexes
        if item.name == "idx_knowledge_chunks_scope_source_key"
    )

    ddl = str(CreateIndex(index).compile(dialect=postgresql.dialect())).lower()

    assert "create unique index" in ddl
    assert "novel_id" in ddl
    assert "coalesce" in ddl
    assert "source_key" in ddl
