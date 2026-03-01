import pytest
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.novel import Chapter, ChapterVersion, Novel, NovelVersion, RewriteRequest, RewriteAnnotation
from app.services.rewrite import service


def _seed_novel_with_chapters(title: str = "rewrite-unit") -> tuple[int, list[int]]:
    db = SessionLocal()
    try:
        novel = Novel(title=title, target_language="zh")
        db.add(novel)
        db.commit()
        db.refresh(novel)
        chapter_ids: list[int] = []
        for idx in range(1, 4):
            c = Chapter(novel_id=novel.id, chapter_num=idx, title=f"第{idx}章 标题", content=f"正文{idx}", status="completed")
            db.add(c)
            db.flush()
            chapter_ids.append(c.id)
        db.commit()
        return novel.id, chapter_ids
    finally:
        db.close()


def test_ensure_default_version_bootstrap_and_sync():
    novel_id, _ = _seed_novel_with_chapters("rewrite-bootstrap")
    db = SessionLocal()
    try:
        version = service.ensure_default_version(db, novel_id)
        db.commit()
        assert version.is_default == 1
        chapters = db.execute(
            select(ChapterVersion).where(ChapterVersion.novel_version_id == version.id).order_by(ChapterVersion.chapter_num.asc())
        ).scalars().all()
        assert len(chapters) == 3

        # Add new chapter and ensure sync appends it.
        db.add(Chapter(novel_id=novel_id, chapter_num=4, title="第4章", content="正文4", status="completed"))
        db.commit()
        version2 = service.ensure_default_version(db, novel_id)
        db.commit()
        chapters2 = db.execute(
            select(ChapterVersion).where(ChapterVersion.novel_version_id == version2.id)
        ).scalars().all()
        assert len(chapters2) == 4
    finally:
        db.close()


def test_get_version_or_default_and_activate():
    novel_id, _ = _seed_novel_with_chapters("rewrite-get-version")
    db = SessionLocal()
    try:
        v1 = service.ensure_default_version(db, novel_id)
        db.add(NovelVersion(novel_id=novel_id, version_no=v1.version_no + 1, status="completed", is_default=0))
        db.commit()
        v2 = db.execute(
            select(NovelVersion).where(NovelVersion.novel_id == novel_id, NovelVersion.version_no == v1.version_no + 1)
        ).scalar_one()
        got = service.get_version_or_default(db, novel_id, None)
        assert got.id == v1.id
        got2 = service.get_version_or_default(db, novel_id, v2.id)
        assert got2.id == v2.id

        service.activate_version(db, novel_id, v2.id)
        db.commit()
        rows = db.execute(select(NovelVersion).where(NovelVersion.novel_id == novel_id)).scalars().all()
        assert sum(1 for x in rows if x.is_default == 1) == 1
        assert any(x.id == v2.id and x.is_default == 1 for x in rows)

        with pytest.raises(ValueError):
            service.get_version_or_default(db, novel_id, 999999)
    finally:
        db.close()


def test_create_target_version_inherits_prefix():
    novel_id, _ = _seed_novel_with_chapters("rewrite-target")
    db = SessionLocal()
    try:
        base = service.ensure_default_version(db, novel_id)
        db.commit()
        target = service.create_target_version(db, novel_id, base, rewrite_from_chapter=3)
        db.commit()
        inherited = db.execute(
            select(ChapterVersion).where(ChapterVersion.novel_version_id == target.id).order_by(ChapterVersion.chapter_num.asc())
        ).scalars().all()
        assert [row.chapter_num for row in inherited] == [1, 2]
        assert target.parent_version_id == base.id
    finally:
        db.close()


def test_validate_annotation_payload_errors():
    text = "abcdefg"
    service.validate_annotation_payload(text, None, None, None)
    with pytest.raises(ValueError):
        service.validate_annotation_payload(text, -1, 2, None)
    with pytest.raises(ValueError):
        service.validate_annotation_payload(text, 5, 3, None)
    with pytest.raises(ValueError):
        service.validate_annotation_payload(text, 1, 3, "xx")
    with pytest.raises(ValueError):
        service.validate_annotation_payload(text, None, None, "notfound")


def test_persist_and_group_annotations_sanitize_values():
    novel_id, _ = _seed_novel_with_chapters("rewrite-annotations")
    db = SessionLocal()
    try:
        base = service.ensure_default_version(db, novel_id)
        target = service.create_target_version(db, novel_id, base, rewrite_from_chapter=2)
        req = RewriteRequest(
            novel_id=novel_id,
            base_version_id=base.id,
            target_version_id=target.id,
            rewrite_from_chapter=2,
            rewrite_to_chapter=3,
            status="submitted",
        )
        db.add(req)
        db.flush()
        service.persist_annotations(
            db,
            req,
            novel_id=novel_id,
            base_version_id=base.id,
            annotations=[
                {"chapter_num": 2, "instruction": "改节奏", "issue_type": "bad_type", "priority": "bad_priority"},
                {"chapter_num": 3, "instruction": "改语气", "issue_type": "style", "priority": "must"},
            ],
        )
        db.commit()
        rows = db.execute(
            select(RewriteAnnotation).where(RewriteAnnotation.rewrite_request_id == req.id).order_by(RewriteAnnotation.chapter_num.asc())
        ).scalars().all()
        assert len(rows) == 2
        assert rows[0].issue_type == "other"
        assert rows[0].priority == "should"
        grouped = service.group_annotations_by_chapter(db, req.id)
        assert set(grouped.keys()) == {2, 3}
    finally:
        db.close()
