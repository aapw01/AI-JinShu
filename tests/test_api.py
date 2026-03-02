"""Basic backend tests for health, presets, novels CRUD, export."""
import pytest
from unittest.mock import patch

from app.core.database import SessionLocal, resolve_novel
from app.models.novel import GenerationCheckpoint, QualityReport


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


def test_export_empty(client):
    r = client.post("/api/novels", json={"title": "Export Test", "target_language": "zh"})
    novel_id = r.json()["id"]
    r = client.get(f"/api/novels/{novel_id}/export?format=txt")
    assert r.status_code == 200
    assert "Export Test" in r.text
    client.delete(f"/api/novels/{novel_id}")


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


def test_longform_quality_and_checkpoints_endpoints(client):
    r = client.post("/api/novels", json={"title": "Longform API Test", "target_language": "zh"})
    novel_id = r.json()["id"]

    qr = client.get(f"/api/novels/{novel_id}/quality-reports")
    assert qr.status_code == 200
    assert isinstance(qr.json(), list)

    cp = client.get(f"/api/novels/{novel_id}/checkpoints")
    assert cp.status_code == 200
    assert isinstance(cp.json(), list)

    vs = client.get(f"/api/novels/{novel_id}/volumes/summary")
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

    l = client.get(f"/api/novels/{novel_id}/feedback")
    assert l.status_code == 200
    arr = l.json()
    assert isinstance(arr, list)
    assert len(arr) >= 1


def test_observability_endpoint(client):
    r = client.post("/api/novels", json={"title": "Observability API Test", "target_language": "zh"})
    novel_id = r.json()["id"]
    obs = client.get(f"/api/novels/{novel_id}/observability")
    assert obs.status_code == 200
    data = obs.json()
    assert "summary" in data
    assert "quality_reports" in data
    assert "checkpoints" in data
    assert "closure_action_oscillation_rate" in data["summary"]
    assert "abrupt_ending_risk" in data["summary"]
