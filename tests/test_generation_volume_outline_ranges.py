from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.novel import ChapterOutline, Novel, NovelVersion
from app.services.generation.agents import OutlinerAgent
from app.services.generation.common import save_full_outlines
from app.services.generation.nodes.init_node import node_outline


def _create_novel_with_version(title: str) -> tuple[Novel, NovelVersion]:
    db = SessionLocal()
    try:
        novel = Novel(title=title, target_language="zh")
        db.add(novel)
        db.flush()
        version = NovelVersion(
            novel_id=novel.id,
            version_no=1,
            status="draft",
            is_default=1,
        )
        db.add(version)
        db.commit()
        db.refresh(novel)
        db.refresh(version)
        return novel, version
    finally:
        db.close()


def _list_outline_nums(novel_id: int, version_id: int) -> list[int]:
    db = SessionLocal()
    try:
        rows = db.execute(
            select(ChapterOutline.chapter_num)
            .where(
                ChapterOutline.novel_id == novel_id,
                ChapterOutline.novel_version_id == version_id,
            )
            .order_by(ChapterOutline.chapter_num)
        ).all()
        return [int(row[0]) for row in rows]
    finally:
        db.close()


def test_outliner_run_full_book_uses_absolute_chapter_numbers(monkeypatch):
    monkeypatch.setattr("app.services.generation.agents.get_llm_with_fallback", lambda *_: object())
    monkeypatch.setattr(
        "app.services.generation.agents._invoke_json_with_schema",
        lambda *_args, **_kwargs: {
            "outlines": [
                {"title": "第31章 第一卷余波", "outline": "承上启下"},
                {"title": "第32章 新局展开", "outline": "推进第二卷"},
            ]
        },
    )

    outlines = OutlinerAgent().run_full_book(
        novel_id="novel-1",
        num_chapters=2,
        prewrite={},
        start_chapter=31,
        language="zh",
    )

    assert [item["chapter_num"] for item in outlines] == [31, 32]
    assert outlines[0]["title"] == "第31章 第一卷余波"
    assert outlines[1]["title"] == "第32章 新局展开"


def test_outliner_run_full_book_fallback_uses_absolute_chapter_numbers(monkeypatch):
    monkeypatch.setattr("app.services.generation.agents.get_llm_with_fallback", lambda *_: object())

    def _raise(*_args, **_kwargs):
        raise RuntimeError("outline llm unavailable")

    monkeypatch.setattr("app.services.generation.agents._invoke_json_with_schema", _raise)

    outlines = OutlinerAgent().run_full_book(
        novel_id="novel-1",
        num_chapters=2,
        prewrite={},
        start_chapter=31,
        language="zh",
    )

    assert [item["chapter_num"] for item in outlines] == [31, 32]
    assert outlines[0]["title"] == "第31章"
    assert outlines[1]["title"] == "第32章"


def test_node_outline_appends_second_volume_without_overwriting_first(monkeypatch):
    novel, version = _create_novel_with_version("volume-outline-append")
    save_full_outlines(
        novel.id,
        [
            {"chapter_num": idx, "title": f"第一卷第{idx}章", "outline": f"第一卷提纲{idx}"}
            for idx in range(1, 31)
        ],
        novel_version_id=version.id,
    )

    class _DummyOutliner:
        def __init__(self):
            self.calls: list[dict] = []

        def run_full_book(self, novel_id, num_chapters, prewrite, start_chapter=1, language="zh", provider=None, model=None):
            self.calls.append(
                {
                    "novel_id": novel_id,
                    "num_chapters": num_chapters,
                    "start_chapter": start_chapter,
                    "language": language,
                }
            )
            return [
                {"chapter_num": idx, "title": f"第二卷第{idx}章", "outline": f"第二卷提纲{idx}"}
                for idx in range(start_chapter, start_chapter + num_chapters)
            ]

    outliner = _DummyOutliner()
    monkeypatch.setattr("app.services.generation.nodes.init_node.progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.generation.nodes.init_node.get_model_for_stage", lambda *_args: ("openai", "mock"))

    result = node_outline(
        {
            "novel_id": novel.id,
            "novel_version_id": version.id,
            "start_chapter": 31,
            "end_chapter": 60,
            "num_chapters": 30,
            "prewrite": {},
            "target_language": "zh",
            "strategy": "web-novel",
            "outliner": outliner,
        }
    )

    assert len(outliner.calls) == 1
    assert outliner.calls[0]["start_chapter"] == 31
    assert _list_outline_nums(novel.id, version.id) == list(range(1, 61))
    assert [item["chapter_num"] for item in result["full_outlines"]] == list(range(1, 61))

    db = SessionLocal()
    try:
        first_chapter = db.execute(
            select(ChapterOutline)
            .where(
                ChapterOutline.novel_id == novel.id,
                ChapterOutline.novel_version_id == version.id,
                ChapterOutline.chapter_num == 1,
            )
        ).scalar_one()
        second_volume_first = db.execute(
            select(ChapterOutline)
            .where(
                ChapterOutline.novel_id == novel.id,
                ChapterOutline.novel_version_id == version.id,
                ChapterOutline.chapter_num == 31,
            )
        ).scalar_one()
        assert first_chapter.title == "第一卷第1章"
        assert first_chapter.outline == "第一卷提纲1"
        assert second_volume_first.title == "第二卷第31章"
        assert second_volume_first.outline == "第二卷提纲31"
    finally:
        db.close()


def test_node_outline_does_not_reuse_when_count_is_enough_but_requested_range_missing(monkeypatch):
    novel, version = _create_novel_with_version("volume-outline-gap")
    save_full_outlines(
        novel.id,
        [
            {"chapter_num": idx, "title": f"已有第{idx}章", "outline": f"已有提纲{idx}"}
            for idx in list(range(1, 31)) + list(range(61, 91))
        ],
        novel_version_id=version.id,
    )

    class _DummyOutliner:
        def __init__(self):
            self.called = 0

        def run_full_book(self, novel_id, num_chapters, prewrite, start_chapter=1, language="zh", provider=None, model=None):
            self.called += 1
            return [
                {"chapter_num": idx, "title": f"补全第{idx}章", "outline": f"补全提纲{idx}"}
                for idx in range(start_chapter, start_chapter + num_chapters)
            ]

    outliner = _DummyOutliner()
    monkeypatch.setattr("app.services.generation.nodes.init_node.progress", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.services.generation.nodes.init_node.get_model_for_stage", lambda *_args: ("openai", "mock"))

    result = node_outline(
        {
            "novel_id": novel.id,
            "novel_version_id": version.id,
            "start_chapter": 31,
            "end_chapter": 60,
            "num_chapters": 30,
            "prewrite": {},
            "target_language": "zh",
            "strategy": "web-novel",
            "outliner": outliner,
        }
    )

    assert outliner.called == 1
    assert [item["chapter_num"] for item in result["full_outlines"]] == list(range(1, 91))
    assert _list_outline_nums(novel.id, version.id) == list(range(1, 91))


def test_node_outline_reuses_existing_requested_range(monkeypatch):
    novel, version = _create_novel_with_version("volume-outline-reuse")
    save_full_outlines(
        novel.id,
        [
            {"chapter_num": idx, "title": f"已有第{idx}章", "outline": f"已有提纲{idx}"}
            for idx in range(1, 61)
        ],
        novel_version_id=version.id,
    )

    class _DummyOutliner:
        def run_full_book(self, *args, **kwargs):  # pragma: no cover - should not be called
            raise AssertionError("outliner should not run when requested range already exists")

    monkeypatch.setattr("app.services.generation.nodes.init_node.progress", lambda *_args, **_kwargs: None)

    result = node_outline(
        {
            "novel_id": novel.id,
            "novel_version_id": version.id,
            "start_chapter": 31,
            "end_chapter": 60,
            "num_chapters": 30,
            "prewrite": {},
            "target_language": "zh",
            "strategy": "web-novel",
            "outliner": _DummyOutliner(),
        }
    )

    assert [item["chapter_num"] for item in result["full_outlines"]] == list(range(1, 61))
