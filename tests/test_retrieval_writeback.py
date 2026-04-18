from __future__ import annotations

from app.services.generation.chapter_commit import _chapter_finalized_knowledge_chunk_handler
from app.services.generation.events import GenerationEvent


def test_chapter_finalized_knowledge_chunk_handler_writes_three_chunk_types(monkeypatch):
    calls: list[dict] = []

    class _Retriever:
        def upsert_chunk(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(
        "app.services.generation.chapter_commit.KnowledgeRetriever",
        lambda: _Retriever(),
    )

    event = GenerationEvent(
        name="chapter.finalized",
        payload={
            "state": {
                "novel_id": 1,
                "novel_version_id": 2,
                "prewrite": {
                    "specification": {
                        "characters": [{"name": "林舟", "role": "主角"}],
                        "world_rules": ["夜里不能点灯"],
                        "plotlines": ["云家主线"],
                    }
                },
            },
            "chapter_num": 3,
            "summary_text": "第3章中林舟确认云家的真正操盘者。",
            "final_content": "林舟在仓库里确认了云家的真正操盘者，并决定当晚潜入。",
            "outline": {
                "title": "仓库真相",
                "chapter_objective": "确认幕后主使",
                "required_new_information": ["云家真正操盘者身份"],
                "relationship_delta": "林舟对苏晚的信任下降",
                "conflict_axis": "查明幕后主使",
            },
            "progression_payload": {
                "advancement": {
                    "chapter_objective": "确认幕后主使",
                    "new_information": ["云家真正操盘者身份"],
                    "relationship_delta": "林舟对苏晚的信任下降",
                },
                "transition": {
                    "ending_scene": "旧仓库门口",
                    "last_action": "林舟决定当晚潜入",
                },
            },
            "extracted_facts": {
                "events": [{"title": "确认幕后主使", "summary": "林舟确认云家操盘者"}],
                "facts": [{"entity_name": "云家", "fact_type": "truth", "value": "真正操盘者浮出水面"}],
            },
        },
    )

    _chapter_finalized_knowledge_chunk_handler(event)

    assert [item["source_type"] for item in calls] == [
        "chapter_summary",
        "chapter_continuity",
        "chapter_fact_delta",
    ]
    assert calls[0]["source_key"] == "chapter_summary:3"
    assert calls[1]["source_key"] == "chapter_continuity:3"
    assert calls[2]["source_key"] == "chapter_fact_delta:3"
    assert "确认幕后主使" in calls[1]["summary"]
    assert "信任下降" in calls[1]["summary"]
    assert "确认幕后主使" in calls[2]["summary"]
    assert "操盘者" in calls[2]["summary"]
