"""Basic backend tests for health, presets, novels CRUD, export."""
import io
import zipfile
import pytest
from unittest.mock import patch
from sqlalchemy import select

from app.core.database import SessionLocal, resolve_novel
from app.models.creation_task import CreationTask
from app.models.novel import ChapterOutline, ChapterVersion, GenerationCheckpoint, NovelVersion, QualityReport


@pytest.fixture
def mock_db():
    with patch("app.api.routes.novels.get_db") as m:
        yield m


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_presets(client):
    r = client.get("/api/presets")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, dict)


def test_novels_crud(client):
    # Create
    r = client.post("/api/novels", json={"title": "Test Novel", "target_language": "zh"})
    assert r.status_code == 200
    data = r.json()
    assert "id" in data
    assert data["title"] == "Test Novel"
    novel_id = data["id"]

    # Get
    r = client.get(f"/api/novels/{novel_id}")
    assert r.status_code == 200
    assert r.json()["title"] == "Test Novel"

    # Update
    r = client.put(f"/api/novels/{novel_id}", json={"title": "Updated"})
    assert r.status_code == 200
    assert r.json()["title"] == "Updated"

    # List
    r = client.get("/api/novels")
    assert r.status_code == 200
    assert len(r.json()) >= 1

    # Delete
    r = client.delete(f"/api/novels/{novel_id}")
    assert r.status_code == 200


def test_export_empty_returns_409(client):
    r = client.post("/api/novels", json={"title": "Export Test", "target_language": "zh"})
    novel_id = r.json()["id"]
    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        default_version = db.execute(
            select(NovelVersion).where(
                NovelVersion.novel_id == novel.id,
                NovelVersion.is_default == 1,
            )
        ).scalar_one()
        version_id = int(default_version.id)
    finally:
        db.close()
    r = client.get(f"/api/novels/{novel_id}/export?format=txt&version_id={version_id}")
    assert r.status_code == 409
    assert "No chapters" in r.text
    client.delete(f"/api/novels/{novel_id}")


def test_export_uses_selected_version_content(client):
    r = client.post("/api/novels", json={"title": "Export Version Test", "target_language": "zh"})
    assert r.status_code == 200
    novel_id = r.json()["id"]

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        v1 = db.execute(
            select(NovelVersion).where(
                NovelVersion.novel_id == novel.id,
                NovelVersion.is_default == 1,
            )
        ).scalar_one()
        v2 = NovelVersion(novel_id=novel.id, version_no=2, status="completed", is_default=0)
        db.add(v2)
        db.flush()
        db.add(
            ChapterVersion(
                novel_version_id=v1.id,
                chapter_num=1,
                title="旧版标题",
                content="这是默认版本正文。",
                status="completed",
            )
        )
        db.add(
            ChapterVersion(
                novel_version_id=v2.id,
                chapter_num=1,
                title="新版标题",
                content="这是第二版本正文。",
                status="completed",
            )
        )
        db.commit()
        v2_id = int(v2.id)
    finally:
        db.close()

    txt = client.get(f"/api/novels/{novel_id}/export?format=txt&version_id={v2_id}")
    assert txt.status_code == 200
    assert "这是第二版本正文。" in txt.text
    assert "这是默认版本正文。" not in txt.text

    md = client.get(f"/api/novels/{novel_id}/export?format=md&version_id={v2_id}")
    assert md.status_code == 200
    assert "这是第二版本正文。" in md.text

    zipped = client.get(f"/api/novels/{novel_id}/export?format=zip&version_id={v2_id}")
    assert zipped.status_code == 200
    with zipfile.ZipFile(io.BytesIO(zipped.content), "r") as zf:
        names = zf.namelist()
        assert "00_版本信息.json" in names
        chapter_files = [name for name in names if name.endswith(".txt") and name.startswith("001_")]
        assert chapter_files
        chapter_text = zf.read(chapter_files[0]).decode("utf-8")
        assert "这是第二版本正文。" in chapter_text


def test_generation_submit_conflict(client):
    r = client.post("/api/novels", json={"title": "Gen Test", "target_language": "zh"})
    novel_id = r.json()["id"]
    r1 = client.post(f"/api/novels/{novel_id}/generate", json={"num_chapters": 3, "start_chapter": 1})
    assert r1.status_code == 200
    r2 = client.post(f"/api/novels/{novel_id}/generate", json={"num_chapters": 2, "start_chapter": 1})
    assert r2.status_code == 409


def test_generation_status_extended_fields(client):
    r = client.post("/api/novels", json={"title": "Status Test", "target_language": "zh"})
    novel_id = r.json()["id"]
    submit = client.post(f"/api/novels/{novel_id}/generate", json={"num_chapters": 2, "start_chapter": 1})
    task_id = submit.json()["task_id"]
    s = client.get(f"/api/novels/{novel_id}/generation/status?task_id={task_id}")
    assert s.status_code == 200
    body = s.json()
    assert "total_chapters" in body
    assert "estimated_cost" in body
    assert "current_phase" in body
    assert "current_subtask" in body
    assert "decision_state" in body
    assert body.get("task_id") == task_id
    assert "error_code" in body
    assert "error_category" in body
    assert "retryable" in body


def test_generation_status_terminal_state_ignores_stale_redis_progress(client, monkeypatch):
    created = client.post("/api/novels", json={"title": "Terminal Status", "target_language": "zh"})
    assert created.status_code == 200
    novel_id = created.json()["id"]

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        row = CreationTask(
            user_uuid="test-admin-user",
            task_type="generation",
            resource_type="novel",
            resource_id=int(novel.id),
            status="completed",
            phase="completed",
            progress=100.0,
            message="db completed",
            payload_json={
                "novel_id": int(novel.id),
                "start_chapter": 1,
                "num_chapters": 15,
                "book_start_chapter": 1,
                "book_target_total_chapters": 15,
            },
            resume_cursor_json={
                "unit_type": "chapter",
                "partition": None,
                "last_completed": 16,
                "next": 17,
                "runtime_state": {
                    "mode": "completed",
                    "volume_no": 1,
                    "segment_start_chapter": 1,
                    "segment_end_chapter": 16,
                    "next_chapter": 17,
                    "book_effective_end_chapter": 16,
                    "book_target_total_chapters": 15,
                    "tail_rewrite_attempts": 2,
                    "bridge_attempts": 1,
                },
            },
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        task_id = row.public_id
    finally:
        db.close()

    monkeypatch.setattr(
        "app.api.routes.generation._redis_get_json",
        lambda _key: {
            "status": "running",
            "run_state": "running",
            "step": "chapter_writing",
            "current_phase": "chapter_writing",
            "current_chapter": 13,
            "total_chapters": 15,
            "progress": 42,
            "message": "stale redis payload",
        },
    )

    status = client.get(f"/api/novels/{novel_id}/generation/status?task_id={task_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "completed"
    assert body["run_state"] == "completed"
    assert body["step"] == "completed"
    assert body["current_phase"] == "completed"
    assert body["current_chapter"] == 16
    assert body["total_chapters"] == 16
    assert body["progress"] == 100
    assert body["message"] == "db completed"


def test_generation_status_prefers_resume_cursor_after_chapter_done(client, monkeypatch):
    created = client.post("/api/novels", json={"title": "Chapter Done Status", "target_language": "zh"})
    assert created.status_code == 200
    novel_id = created.json()["id"]

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        row = CreationTask(
            user_uuid="test-admin-user",
            task_type="generation",
            resource_type="novel",
            resource_id=int(novel.id),
            status="running",
            phase="chapter_done",
            progress=38.0,
            payload_json={
                "novel_id": int(novel.id),
                "start_chapter": 1,
                "num_chapters": 15,
                "book_start_chapter": 1,
                "book_target_total_chapters": 15,
            },
            resume_cursor_json={
                "unit_type": "chapter",
                "partition": None,
                "last_completed": 1,
                "next": 2,
                "runtime_state": {
                    "mode": "segment_running",
                    "volume_no": 1,
                    "segment_start_chapter": 1,
                    "segment_end_chapter": 15,
                    "next_chapter": 2,
                    "book_effective_end_chapter": 15,
                    "book_target_total_chapters": 15,
                    "tail_rewrite_attempts": 0,
                    "bridge_attempts": 0,
                },
            },
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        task_id = row.public_id
    finally:
        db.close()

    monkeypatch.setattr(
        "app.api.routes.generation._redis_get_json",
        lambda _key: {
            "status": "running",
            "run_state": "running",
            "step": "chapter_done",
            "current_phase": "chapter_done",
            "current_chapter": 1,
            "total_chapters": 15,
            "progress": 42,
            "message": "第1章完成",
        },
    )

    status = client.get(f"/api/novels/{novel_id}/generation/status?task_id={task_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "running"
    assert body["step"] == "chapter_done"
    assert body["current_phase"] == "chapter_done"
    assert body["current_chapter"] == 2
    assert body["total_chapters"] == 15


def test_generation_status_uses_creation_task_outline_confirmation_state(client, monkeypatch):
    created = client.post("/api/novels", json={"title": "Outline Pending", "target_language": "zh"})
    assert created.status_code == 200
    novel_id = created.json()["id"]

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        row = CreationTask(
            user_uuid="test-admin-user",
            task_type="generation",
            resource_type="novel",
            resource_id=int(novel.id),
            status="running",
            phase="outline_ready",
            progress=20.0,
            payload_json={
                "novel_id": int(novel.id),
                "start_chapter": 1,
                "num_chapters": 8,
                "awaiting_outline_confirmation": True,
                "outline_confirmed": False,
            },
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        task_id = row.public_id
    finally:
        db.close()

    monkeypatch.setattr("app.api.routes.generation._redis_get_json", lambda _key: None)
    status = client.get(f"/api/novels/{novel_id}/generation/status?task_id={task_id}")
    assert status.status_code == 200
    body = status.json()
    assert body["status"] == "awaiting_outline_confirmation"
    assert body["run_state"] == "awaiting_outline_confirmation"
    assert body["current_phase"] == "outline_ready"
    assert body["current_chapter"] == 1
    assert body["total_chapters"] == 8


def test_confirm_outline_generation_updates_creation_task(client):
    created = client.post("/api/novels", json={"title": "Confirm Outline", "target_language": "zh"})
    assert created.status_code == 200
    novel_id = created.json()["id"]

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        row = CreationTask(
            user_uuid="test-admin-user",
            task_type="generation",
            resource_type="novel",
            resource_id=int(novel.id),
            status="running",
            phase="outline_ready",
            worker_task_id="worker-outline-confirm",
            progress=20.0,
            payload_json={
                "novel_id": int(novel.id),
                "start_chapter": 1,
                "num_chapters": 6,
                "awaiting_outline_confirmation": True,
                "outline_confirmed": False,
            },
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        task_id = row.public_id
    finally:
        db.close()

    resp = client.post(f"/api/novels/{novel_id}/generation/{task_id}/confirm-outline")
    assert resp.status_code == 200

    db = SessionLocal()
    try:
        row = db.execute(select(CreationTask).where(CreationTask.public_id == task_id)).scalar_one()
        payload = row.payload_json if isinstance(row.payload_json, dict) else {}
    finally:
        db.close()

    assert payload.get("awaiting_outline_confirmation") is False
    assert payload.get("outline_confirmed") is True
    assert row.phase == "chapter_writing"
    assert row.message == "已确认大纲，继续生成章节"


def test_confirm_outline_generation_accepts_worker_task_id(client):
    created = client.post("/api/novels", json={"title": "Confirm Outline Worker Id", "target_language": "zh"})
    assert created.status_code == 200
    novel_id = created.json()["id"]

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        row = CreationTask(
            user_uuid="test-admin-user",
            task_type="generation",
            resource_type="novel",
            resource_id=int(novel.id),
            status="running",
            phase="outline_ready",
            worker_task_id="legacy-worker-outline",
            progress=20.0,
            payload_json={
                "novel_id": int(novel.id),
                "start_chapter": 1,
                "num_chapters": 6,
                "awaiting_outline_confirmation": True,
                "outline_confirmed": False,
            },
        )
        db.add(row)
        db.flush()
        task_id = row.public_id
        from app.models.novel import GenerationTask

        db.add(
            GenerationTask(
                task_id="legacy-worker-outline",
                novel_id=int(novel.id),
                status="awaiting_outline_confirmation",
                run_state="running",
                current_phase="outline_ready",
                current_chapter=1,
                total_chapters=6,
                num_chapters=6,
                start_chapter=1,
                outline_confirmed=0,
            )
        )
        db.commit()
    finally:
        db.close()

    resp = client.post(f"/api/novels/{novel_id}/generation/legacy-worker-outline/confirm-outline")
    assert resp.status_code == 200

    db = SessionLocal()
    try:
        row = db.execute(select(CreationTask).where(CreationTask.public_id == task_id)).scalar_one()
        payload = row.payload_json if isinstance(row.payload_json, dict) else {}
    finally:
        db.close()

    assert payload.get("awaiting_outline_confirmation") is False
    assert payload.get("outline_confirmed") is True
    assert row.phase == "chapter_writing"


def test_version_scoped_endpoints_require_version_id(client):
    r = client.post("/api/novels", json={"title": "Version Scope", "target_language": "zh"})
    assert r.status_code == 200
    novel_id = r.json()["id"]

    chapters = client.get(f"/api/novels/{novel_id}/chapters")
    assert chapters.status_code == 400
    assert chapters.json()["detail"]["error_code"] == "missing_version_id"

    progress = client.get(f"/api/novels/{novel_id}/chapter-progress")
    assert progress.status_code == 400
    assert progress.json()["detail"]["error_code"] == "missing_version_id"

    export = client.get(f"/api/novels/{novel_id}/export?format=txt")
    assert export.status_code == 400
    assert export.json()["detail"]["error_code"] == "missing_version_id"

    summary = client.get(f"/api/novels/{novel_id}/volumes/summary")
    assert summary.status_code == 400
    assert summary.json()["detail"]["error_code"] == "missing_version_id"

    observability = client.get(f"/api/novels/{novel_id}/observability")
    assert observability.status_code == 400
    assert observability.json()["detail"]["error_code"] == "missing_version_id"


def test_chapter_response_contains_word_count(client):
    r = client.post("/api/novels", json={"title": "Word Count Test", "target_language": "zh"})
    assert r.status_code == 200
    novel_id = r.json()["id"]
    version_id: int | None = None

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        version = db.execute(
            select(NovelVersion).where(
                NovelVersion.novel_id == novel.id,
                NovelVersion.is_default == 1,
            )
        ).scalar_one()
        version_id = int(version.id)
        db.add(
            ChapterVersion(
                novel_version_id=version_id,
                chapter_num=1,
                title="第1章 测试",
                content="测试 文本 123",
                status="completed",
            )
        )
        db.commit()
    finally:
        db.close()

    assert version_id is not None
    res = client.get(f"/api/novels/{novel_id}/chapters?version_id={version_id}")
    assert res.status_code == 200
    payload = res.json()
    assert payload
    assert payload[0]["word_count"] == 7
    assert payload[0]["version_id"] == version_id


def test_chapter_progress_includes_volume_fields_with_configured_volume_size(client):
    r = client.post(
        "/api/novels",
        json={"title": "Volume Progress Configured", "target_language": "zh", "config": {"volume_size": 12}},
    )
    assert r.status_code == 200
    novel_id = r.json()["id"]
    version_id: int | None = None

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        version = db.execute(
            select(NovelVersion).where(
                NovelVersion.novel_id == novel.id,
                NovelVersion.is_default == 1,
            )
        ).scalar_one()
        version_id = int(version.id)
        db.add(
            ChapterVersion(
                novel_version_id=version_id,
                chapter_num=1,
                title="第1章",
                content="正文",
                status="completed",
            )
        )
        db.add(
            ChapterVersion(
                novel_version_id=version_id,
                chapter_num=13,
                title="第13章",
                content="正文",
                status="completed",
            )
        )
        db.commit()
    finally:
        db.close()

    assert version_id is not None
    resp = client.get(f"/api/novels/{novel_id}/chapter-progress?version_id={version_id}")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    by_num = {int(item["chapter_num"]): item for item in items}
    assert by_num[1]["volume_size"] == 12
    assert by_num[1]["volume_no"] == 1
    assert by_num[13]["volume_size"] == 12
    assert by_num[13]["volume_no"] == 2


def test_chapter_progress_defaults_to_volume_size_30_when_not_configured(client):
    r = client.post(
        "/api/novels",
        json={"title": "Volume Progress Default", "target_language": "zh"},
    )
    assert r.status_code == 200
    novel_id = r.json()["id"]
    version_id: int | None = None

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        version = db.execute(
            select(NovelVersion).where(
                NovelVersion.novel_id == novel.id,
                NovelVersion.is_default == 1,
            )
        ).scalar_one()
        version_id = int(version.id)
        db.add(
            ChapterVersion(
                novel_version_id=version_id,
                chapter_num=31,
                title="第31章",
                content="正文",
                status="completed",
            )
        )
        db.commit()
    finally:
        db.close()

    assert version_id is not None
    resp = client.get(f"/api/novels/{novel_id}/chapter-progress?version_id={version_id}")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    item = items[0]
    assert item["chapter_num"] == 31
    assert item["volume_size"] == 30
    assert item["volume_no"] == 2


def test_chapter_progress_maps_consistency_blocked_to_blocked(client):
    r = client.post("/api/novels", json={"title": "Blocked Progress", "target_language": "zh"})
    assert r.status_code == 200
    novel_id = r.json()["id"]
    version_id: int | None = None

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        version = db.execute(
            select(NovelVersion).where(
                NovelVersion.novel_id == novel.id,
                NovelVersion.is_default == 1,
            )
        ).scalar_one()
        version_id = int(version.id)
        db.add(
            ChapterOutline(
                novel_id=novel.id,
                novel_version_id=version_id,
                chapter_num=1,
                title="第1章",
            )
        )
        db.add(
            ChapterVersion(
                novel_version_id=version_id,
                chapter_num=1,
                title="第1章",
                content="",
                status="consistency_blocked",
            )
        )
        db.commit()
    finally:
        db.close()

    assert version_id is not None
    resp = client.get(f"/api/novels/{novel_id}/chapter-progress?version_id={version_id}")
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["status"] == "blocked"


def test_chapter_progress_uses_creation_task_resume_cursor_when_redis_missing(client, monkeypatch):
    r = client.post("/api/novels", json={"title": "Progress Resume Cursor", "target_language": "zh"})
    assert r.status_code == 200
    novel_id = r.json()["id"]
    version_id: int | None = None

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        version = db.execute(
            select(NovelVersion).where(
                NovelVersion.novel_id == novel.id,
                NovelVersion.is_default == 1,
            )
        ).scalar_one()
        version_id = int(version.id)
        db.add_all(
            [
                ChapterOutline(novel_id=novel.id, novel_version_id=version_id, chapter_num=1, title="第1章"),
                ChapterOutline(novel_id=novel.id, novel_version_id=version_id, chapter_num=2, title="第2章"),
                ChapterOutline(novel_id=novel.id, novel_version_id=version_id, chapter_num=3, title="第3章"),
            ]
        )
        db.add(
            CreationTask(
                user_uuid="test-admin-user",
                task_type="generation",
                resource_type="novel",
                resource_id=int(novel.id),
                status="running",
                payload_json={
                    "novel_id": int(novel.id),
                    "start_chapter": 1,
                    "num_chapters": 3,
                    "book_start_chapter": 1,
                    "book_target_total_chapters": 3,
                },
                resume_cursor_json={
                    "unit_type": "chapter",
                    "partition": None,
                    "last_completed": 1,
                    "next": 2,
                    "runtime_state": {
                        "mode": "segment_running",
                        "volume_no": 1,
                        "segment_start_chapter": 1,
                        "segment_end_chapter": 3,
                        "next_chapter": 2,
                        "book_effective_end_chapter": 3,
                        "book_target_total_chapters": 3,
                        "tail_rewrite_attempts": 0,
                        "bridge_attempts": 0,
                    },
                },
            )
        )
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr("app.api.routes.chapters._get_generating_chapter_from_redis", lambda _novel_id: None)
    assert version_id is not None
    resp = client.get(f"/api/novels/{novel_id}/chapter-progress?version_id={version_id}")
    assert resp.status_code == 200
    by_num = {int(item["chapter_num"]): item for item in resp.json()}
    assert by_num[1]["status"] == "pending"
    assert by_num[2]["status"] == "generating"
    assert by_num[3]["status"] == "pending"


def test_generation_tasks_list_preserves_paused_status_over_outline_pending_flag(client):
    created = client.post("/api/novels", json={"title": "Task List Paused", "target_language": "zh"})
    assert created.status_code == 200
    novel_id = created.json()["id"]

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        row = CreationTask(
            user_uuid="test-admin-user",
            task_type="generation",
            resource_type="novel",
            resource_id=int(novel.id),
            status="paused",
            phase="paused",
            progress=20.0,
            payload_json={
                "novel_id": int(novel.id),
                "start_chapter": 1,
                "num_chapters": 5,
                "awaiting_outline_confirmation": True,
                "outline_confirmed": False,
            },
            resume_cursor_json={"next": 1},
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    resp = client.get(f"/api/novels/{novel_id}/generation/tasks")
    assert resp.status_code == 200
    items = resp.json()
    assert items
    assert items[0]["status"] == "paused"
    assert items[0]["run_state"] == "paused"


def test_generation_tasks_list_prefers_resume_cursor_after_chapter_done(client, monkeypatch):
    created = client.post("/api/novels", json={"title": "Task List Chapter Done", "target_language": "zh"})
    assert created.status_code == 200
    novel_id = created.json()["id"]

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        row = CreationTask(
            user_uuid="test-admin-user",
            task_type="generation",
            resource_type="novel",
            resource_id=int(novel.id),
            status="running",
            phase="chapter_done",
            progress=42.0,
            payload_json={
                "novel_id": int(novel.id),
                "start_chapter": 1,
                "num_chapters": 15,
                "book_start_chapter": 1,
                "book_target_total_chapters": 15,
            },
            resume_cursor_json={
                "unit_type": "chapter",
                "partition": None,
                "last_completed": 1,
                "next": 2,
                "runtime_state": {
                    "mode": "segment_running",
                    "volume_no": 1,
                    "segment_start_chapter": 1,
                    "segment_end_chapter": 15,
                    "next_chapter": 2,
                    "book_effective_end_chapter": 15,
                    "book_target_total_chapters": 15,
                    "tail_rewrite_attempts": 0,
                    "bridge_attempts": 0,
                },
            },
        )
        db.add(row)
        db.commit()
    finally:
        db.close()

    monkeypatch.setattr(
        "app.api.routes.generation._redis_get_json",
        lambda _key: {
            "status": "running",
            "run_state": "running",
            "step": "chapter_done",
            "current_phase": "chapter_done",
            "current_chapter": 1,
            "total_chapters": 15,
            "progress": 42,
            "message": "第1章完成",
        },
    )

    resp = client.get(f"/api/novels/{novel_id}/generation/tasks")
    assert resp.status_code == 200
    items = resp.json()
    assert items
    assert items[0]["current_chapter"] == 2
    assert items[0]["total_chapters"] == 15


def test_longform_quality_and_checkpoints_endpoints(client):
    r = client.post("/api/novels", json={"title": "Longform API Test", "target_language": "zh"})
    novel_id = r.json()["id"]

    qr = client.get(f"/api/novels/{novel_id}/quality-reports")
    assert qr.status_code == 200
    assert isinstance(qr.json(), list)

    cp = client.get(f"/api/novels/{novel_id}/checkpoints")
    assert cp.status_code == 200
    assert isinstance(cp.json(), list)

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        version = db.execute(
            select(NovelVersion).where(
                NovelVersion.novel_id == novel.id,
                NovelVersion.is_default == 1,
            )
        ).scalar_one()
        version_id = int(version.id)
    finally:
        db.close()

    vs = client.get(f"/api/novels/{novel_id}/volumes/summary?version_id={version_id}")
    assert vs.status_code == 200
    assert isinstance(vs.json(), list)


def test_volume_gate_report_endpoint(client):
    r = client.post("/api/novels", json={"title": "Volume Gate API Test", "target_language": "zh"})
    novel_public_id = r.json()["id"]

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_public_id)
        assert novel is not None
        db.add(
            QualityReport(
                novel_id=novel.id,
                scope="volume",
                scope_id="1",
                metrics_json={"avg_review_score": 0.61, "evidence_chain": [{"metric": "avg_review_score"}]},
                verdict="warning",
            )
        )
        db.add(
            GenerationCheckpoint(
                task_id="task-v1",
                novel_id=novel.id,
                volume_no=1,
                chapter_num=30,
                node="volume_gate",
                state_json={"verdict": "warning", "evidence_chain": [{"metric": "avg_review_score"}]},
            )
        )
        db.commit()
    finally:
        db.close()

    rep = client.get(f"/api/novels/{novel_public_id}/volumes/1/gate-report")
    assert rep.status_code == 200
    data = rep.json()
    assert data["volume_no"] == 1
    assert data["verdict"] == "warning"
    assert data["evidence_chain"]


def test_feedback_endpoints(client):
    r = client.post("/api/novels", json={"title": "Feedback API Test", "target_language": "zh"})
    novel_id = r.json()["id"]

    c = client.post(
        f"/api/novels/{novel_id}/feedback",
        json={
            "chapter_num": 3,
            "volume_no": 1,
            "feedback_type": "editor",
            "rating": 0.82,
            "tags": ["节奏", "人物张力"],
            "comment": "中段可再紧凑一些",
        },
    )
    assert c.status_code == 200
    body = c.json()
    assert body["volume_no"] == 1
    assert body["tags"]

    list_resp = client.get(f"/api/novels/{novel_id}/feedback")
    assert list_resp.status_code == 200
    arr = list_resp.json()
    assert isinstance(arr, list)
    assert len(arr) >= 1


def test_observability_endpoint(client):
    r = client.post("/api/novels", json={"title": "Observability API Test", "target_language": "zh"})
    novel_id = r.json()["id"]
    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        version = db.execute(
            select(NovelVersion).where(
                NovelVersion.novel_id == novel.id,
                NovelVersion.is_default == 1,
            )
        ).scalar_one()
        version_id = int(version.id)
    finally:
        db.close()
    obs = client.get(f"/api/novels/{novel_id}/observability?version_id={version_id}")
    assert obs.status_code == 200
    data = obs.json()
    assert "summary" in data
    assert "quality_reports" in data
    assert "checkpoints" in data
    assert "closure_action_oscillation_rate" in data["summary"]
    assert "abrupt_ending_risk" in data["summary"]


def test_generation_status_reads_public_snapshot_instead_of_worker_snapshot(client, monkeypatch):
    created = client.post("/api/novels", json={"title": "Status Snapshot Source", "target_language": "zh"})
    assert created.status_code == 200
    novel_id = created.json()["id"]

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        row = CreationTask(
            user_uuid="test-admin-user",
            task_type="generation",
            resource_type="novel",
            resource_id=int(novel.id),
            status="running",
            phase="chapter_writing",
            progress=12.0,
            worker_task_id="worker-status-stale",
            payload_json={
                "novel_id": int(novel.id),
                "start_chapter": 1,
                "num_chapters": 200,
                "book_start_chapter": 1,
                "book_target_total_chapters": 200,
            },
            resume_cursor_json={
                "next": 5,
                "runtime_state": {
                    "mode": "segment_running",
                    "volume_no": 1,
                    "segment_start_chapter": 1,
                    "segment_end_chapter": 30,
                    "next_chapter": 5,
                    "book_effective_end_chapter": 200,
                    "book_target_total_chapters": 200,
                },
            },
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        task_id = row.public_id
    finally:
        db.close()

    seen_keys: list[str] = []

    def fake_get(key: str):
        seen_keys.append(key)
        if key.endswith(task_id):
            return {
                "status": "running",
                "run_state": "running",
                "current_phase": "chapter_writing",
                "step": "chapter_writing",
                "current_chapter": 5,
                "total_chapters": 200,
                "progress": 12,
                "message": "public snapshot",
            }
        if key.endswith("worker-status-stale"):
            return {
                "status": "running",
                "run_state": "running",
                "current_phase": "chapter_writing",
                "step": "chapter_writing",
                "current_chapter": 1,
                "total_chapters": 200,
                "progress": 12,
                "message": "worker stale snapshot",
            }
        return None

    monkeypatch.setattr("app.api.routes.generation._redis_get_json", fake_get)
    resp = client.get(f"/api/novels/{novel_id}/generation/status?task_id={task_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current_chapter"] == 5
    assert body["total_chapters"] == 200
    assert seen_keys == [f"generation:{task_id}"]


def test_generation_tasks_list_reads_public_snapshot_instead_of_worker_snapshot(client, monkeypatch):
    created = client.post("/api/novels", json={"title": "Task List Snapshot Source", "target_language": "zh"})
    assert created.status_code == 200
    novel_id = created.json()["id"]

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        row = CreationTask(
            user_uuid="test-admin-user",
            task_type="generation",
            resource_type="novel",
            resource_id=int(novel.id),
            status="running",
            phase="chapter_writing",
            progress=18.0,
            worker_task_id="worker-list-stale",
            payload_json={
                "novel_id": int(novel.id),
                "start_chapter": 1,
                "num_chapters": 200,
                "book_start_chapter": 1,
                "book_target_total_chapters": 200,
            },
            resume_cursor_json={
                "next": 5,
                "runtime_state": {
                    "mode": "segment_running",
                    "volume_no": 1,
                    "segment_start_chapter": 1,
                    "segment_end_chapter": 30,
                    "next_chapter": 5,
                    "book_effective_end_chapter": 200,
                    "book_target_total_chapters": 200,
                },
            },
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        task_id = row.public_id
    finally:
        db.close()

    def fake_get(key: str):
        if key.endswith(task_id):
            return {
                "status": "running",
                "run_state": "running",
                "current_phase": "chapter_writing",
                "step": "chapter_writing",
                "current_chapter": 5,
                "total_chapters": 200,
                "progress": 18,
                "message": "public snapshot",
            }
        if key.endswith("worker-list-stale"):
            return {
                "status": "running",
                "run_state": "running",
                "current_phase": "chapter_writing",
                "step": "chapter_writing",
                "current_chapter": 1,
                "total_chapters": 200,
                "progress": 18,
                "message": "worker stale snapshot",
            }
        return None

    monkeypatch.setattr("app.api.routes.generation._redis_get_json", fake_get)
    resp = client.get(f"/api/novels/{novel_id}/generation/tasks")
    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["current_chapter"] == 5
    assert body[0]["total_chapters"] == 200


def test_chapter_progress_uses_active_task_snapshot_not_novel_cache(client, monkeypatch):
    created = client.post("/api/novels", json={"title": "Chapter Progress Snapshot", "target_language": "zh"})
    assert created.status_code == 200
    novel_id = created.json()["id"]
    version_id: int | None = None
    task_id: str | None = None

    db = SessionLocal()
    try:
        novel = resolve_novel(db, novel_id)
        assert novel is not None
        version = db.execute(
            select(NovelVersion).where(
                NovelVersion.novel_id == novel.id,
                NovelVersion.is_default == 1,
            )
        ).scalar_one()
        version_id = int(version.id)
        db.add_all(
            [
                ChapterOutline(novel_id=novel.id, novel_version_id=version_id, chapter_num=1, title="第1章"),
                ChapterOutline(novel_id=novel.id, novel_version_id=version_id, chapter_num=2, title="第2章"),
                ChapterOutline(novel_id=novel.id, novel_version_id=version_id, chapter_num=3, title="第3章"),
                ChapterOutline(novel_id=novel.id, novel_version_id=version_id, chapter_num=4, title="第4章"),
                ChapterOutline(novel_id=novel.id, novel_version_id=version_id, chapter_num=5, title="第5章"),
            ]
        )
        row = CreationTask(
            user_uuid="test-admin-user",
            task_type="generation",
            resource_type="novel",
            resource_id=int(novel.id),
            status="running",
            phase="chapter_writing",
            progress=22.0,
            worker_task_id="worker-progress-stale",
            payload_json={
                "novel_id": int(novel.id),
                "start_chapter": 1,
                "num_chapters": 5,
                "book_start_chapter": 1,
                "book_target_total_chapters": 5,
            },
            resume_cursor_json={
                "next": 5,
                "runtime_state": {
                    "mode": "segment_running",
                    "volume_no": 1,
                    "segment_start_chapter": 1,
                    "segment_end_chapter": 5,
                    "next_chapter": 5,
                    "book_effective_end_chapter": 5,
                    "book_target_total_chapters": 5,
                },
            },
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        task_id = row.public_id
    finally:
        db.close()

    def should_not_call(_novel_id: int):
        raise AssertionError("chapter-progress should not read novel redis cache")

    monkeypatch.setattr("app.api.routes.chapters._get_generating_chapter_from_redis", should_not_call)

    def fake_read_generation_cache(key: str):
        if key.endswith(task_id or ""):
            return {
                "status": "running",
                "run_state": "running",
                "current_phase": "chapter_writing",
                "step": "chapter_writing",
                "current_chapter": 5,
                "total_chapters": 5,
                "progress": 22,
            }
        if key.endswith(str(novel_id)):
            return {
                "status": "running",
                "run_state": "running",
                "current_phase": "chapter_writing",
                "step": "chapter_writing",
                "current_chapter": 1,
                "total_chapters": 5,
                "progress": 22,
            }
        return None

    monkeypatch.setattr("app.services.generation.status_snapshot.read_generation_cache", fake_read_generation_cache)
    assert version_id is not None
    resp = client.get(f"/api/novels/{novel_id}/chapter-progress?version_id={version_id}")
    assert resp.status_code == 200
    by_num = {int(item["chapter_num"]): item for item in resp.json()}
    assert by_num[5]["status"] == "generating"
