from app.services.memory.context import select_context_candidates


def test_select_context_candidates_prefers_outline_relevant_entries():
    candidates = [
        {"id": "a", "content": "林秋在旧宅门口调查账册真相。"},
        {"id": "b", "content": "无关的集市闲谈和路人对话。"},
        {"id": "c", "content": "陆沉在医院处理上一章留下的伤势。"},
    ]

    selected = select_context_candidates(
        chapter_num=9,
        outline={"title": "旧宅夜探", "outline": "林秋夜探旧宅，寻找账册", "chapter_objective": "找到账册"},
        candidates=candidates,
        max_items=2,
    )

    assert [item["id"] for item in selected] == ["a", "c"]


def test_select_context_candidates_soft_fails_on_bad_input():
    selected = select_context_candidates(
        chapter_num=9,
        outline={"title": "旧宅夜探", "outline": "林秋夜探旧宅"},
        candidates=[None, "oops", {"id": "a"}],
        max_items=2,
    )

    assert selected == [{"id": "a"}]
