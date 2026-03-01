def test_generation_pause_resume_cancel_and_list(client):
    r = client.post("/api/novels", json={"title": "Control Test", "target_language": "zh"})
    assert r.status_code == 200
    novel_id = r.json()["id"]

    s = client.post(
        f"/api/novels/{novel_id}/generate",
        json={"num_chapters": 3, "start_chapter": 1, "idempotency_key": "idem-1"},
    )
    assert s.status_code == 200
    task_id = s.json()["task_id"]
    assert task_id

    tasks = client.get(f"/api/novels/{novel_id}/generation/tasks")
    assert tasks.status_code == 200
    assert len(tasks.json()) >= 1
    assert any(t["task_id"] == task_id for t in tasks.json())

    pause = client.post(f"/api/novels/{novel_id}/generation/pause?task_id={task_id}")
    assert pause.status_code == 200
    assert pause.json()["run_state"] == "paused"

    resume = client.post(f"/api/novels/{novel_id}/generation/resume?task_id={task_id}")
    assert resume.status_code == 200
    assert resume.json()["run_state"] == "queued"

    cancel = client.post(f"/api/novels/{novel_id}/generation/cancel?task_id={task_id}")
    assert cancel.status_code == 200
    assert cancel.json()["run_state"] == "cancelled"
