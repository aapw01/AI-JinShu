from sqlalchemy import select

from app.core.database import SessionLocal, resolve_novel
from app.models.novel import Chapter, RewriteRequest


def _seed_chapters(novel_public_id: str, count: int = 3):
    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_public_id)
        assert novel is not None
        for i in range(1, count + 1):
            db.add(
                Chapter(
                    novel_id=novel.id,
                    chapter_num=i,
                    title=f"第{i}章",
                    content=f"这是第{i}章内容。主角受伤。",
                    summary=f"摘要{i}",
                    status="completed",
                )
            )
        db.commit()
    finally:
        db.close()


def test_versions_bootstrap_and_activate(client):
    r = client.post("/api/novels", json={"title": "Version Test", "target_language": "zh"})
    novel_id = r.json()["id"]
    _seed_chapters(novel_id, 2)

    versions = client.get(f"/api/novels/{novel_id}/versions")
    assert versions.status_code == 200
    items = versions.json()
    assert len(items) >= 1
    assert items[0]["version_no"] == 1

    v_id = items[0]["id"]
    act = client.post(f"/api/novels/{novel_id}/versions/{v_id}/activate")
    assert act.status_code == 200
    assert act.json()["ok"] is True


def test_create_rewrite_request_and_rewrite_from(client):
    r = client.post("/api/novels", json={"title": "Rewrite Test", "target_language": "zh"})
    novel_id = r.json()["id"]
    _seed_chapters(novel_id, 5)

    versions = client.get(f"/api/novels/{novel_id}/versions").json()
    base_version_id = versions[0]["id"]

    create = client.post(
        f"/api/novels/{novel_id}/rewrite-requests",
        json={
            "base_version_id": base_version_id,
            "annotations": [
                {
                    "chapter_num": 4,
                    "selected_text": "主角受伤",
                    "instruction": "这段改成腿伤，后续行动受限",
                    "issue_type": "continuity",
                    "priority": "must",
                },
                {
                    "chapter_num": 2,
                    "selected_text": "主角受伤",
                    "instruction": "提前埋下旧伤伏笔",
                    "issue_type": "style",
                    "priority": "should",
                },
            ],
        },
    )
    assert create.status_code == 200
    body = create.json()
    assert body["status"] == "queued"
    assert body["rewrite_from_chapter"] == 2
    assert body["rewrite_to_chapter"] == 5

    status = client.get(f"/api/novels/{novel_id}/rewrite-requests/{body['id']}/status")
    assert status.status_code == 200


def test_rewrite_annotation_selected_text_validation(client):
    r = client.post("/api/novels", json={"title": "Rewrite Validation", "target_language": "zh"})
    novel_id = r.json()["id"]
    _seed_chapters(novel_id, 2)

    versions = client.get(f"/api/novels/{novel_id}/versions").json()
    base_version_id = versions[0]["id"]

    create = client.post(
        f"/api/novels/{novel_id}/rewrite-requests",
        json={
            "base_version_id": base_version_id,
            "annotations": [
                {
                    "chapter_num": 1,
                    "selected_text": "不存在文本",
                    "instruction": "测试",
                }
            ],
        },
    )
    assert create.status_code == 400
    assert "selected_text_not_found" in create.text


def test_retry_rewrite_request(client):
    r = client.post("/api/novels", json={"title": "Rewrite Retry", "target_language": "zh"})
    novel_id = r.json()["id"]
    _seed_chapters(novel_id, 2)
    versions = client.get(f"/api/novels/{novel_id}/versions").json()
    base_version_id = versions[0]["id"]

    create = client.post(
        f"/api/novels/{novel_id}/rewrite-requests",
        json={
            "base_version_id": base_version_id,
            "annotations": [
                {
                    "chapter_num": 1,
                    "selected_text": "主角受伤",
                    "instruction": "改成轻伤",
                }
            ],
        },
    )
    req_id = create.json()["id"]

    db = SessionLocal()
    try:
        row = db.execute(select(RewriteRequest).where(RewriteRequest.id == req_id)).scalar_one_or_none()
        assert row is not None
        row.status = "failed"
        db.commit()
    finally:
        db.close()

    retry = client.post(f"/api/novels/{novel_id}/rewrite-requests/{req_id}/retry")
    assert retry.status_code == 200
    assert retry.json()["status"] == "queued"
