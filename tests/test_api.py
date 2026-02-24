"""Basic backend tests for health, presets, novels CRUD, export."""
import pytest
from unittest.mock import patch


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
    with patch("app.api.routes.generation.submit_generation_task.delay") as delay:
        delay.return_value.id = "task-1"
        r1 = client.post(f"/api/novels/{novel_id}/generate", json={"num_chapters": 3, "start_chapter": 1})
        assert r1.status_code == 200
        r2 = client.post(f"/api/novels/{novel_id}/generate", json={"num_chapters": 2, "start_chapter": 1})
        assert r2.status_code == 409


def test_generation_status_extended_fields(client):
    r = client.post("/api/novels", json={"title": "Status Test", "target_language": "zh"})
    novel_id = r.json()["id"]
    with patch("app.api.routes.generation.submit_generation_task.delay") as delay:
        delay.return_value.id = "task-2"
        client.post(f"/api/novels/{novel_id}/generate", json={"num_chapters": 2, "start_chapter": 1})
    s = client.get(f"/api/novels/{novel_id}/generation/status?task_id=task-2")
    assert s.status_code == 200
    body = s.json()
    assert "total_chapters" in body
    assert "estimated_cost" in body
    assert "current_phase" in body
