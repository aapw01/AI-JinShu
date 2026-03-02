from sqlalchemy import select

from app.core.authn import create_access_token
from app.core.database import SessionLocal
from app.models.novel import Novel, NovelVersion, StoryCharacterProfile, User
from app.models.storyboard import StoryboardProject
from app.models.storyboard import StoryboardShot, StoryboardVersion


def _ensure_user(user_uuid: str, role: str = "user", status: str = "active") -> None:
    db = SessionLocal()
    try:
        if not db.execute(select(User).where(User.uuid == user_uuid)).scalar_one_or_none():
            db.add(User(uuid=user_uuid, email=f"{user_uuid}@test.local", password_hash="x", role=role, status=status))
            db.commit()
    finally:
        db.close()


def _auth_headers(user_uuid: str, role: str = "user", status: str = "active") -> dict[str, str]:
    _ensure_user(user_uuid, role=role, status=status)
    token = create_access_token(user_uuid, role=role, status=status)
    return {"Authorization": f"Bearer {token}"}


def test_storyboard_create_and_generate_flow(anon_client):
    owner_headers = _auth_headers("story-owner", role="user")

    create_novel = anon_client.post(
        "/api/novels",
        json={"title": "短剧样本", "target_language": "zh"},
        headers=owner_headers,
    )
    assert create_novel.status_code == 200
    novel_public_id = create_novel.json()["id"]

    db = SessionLocal()
    try:
        novel = db.execute(select(Novel).where(Novel.uuid == novel_public_id)).scalar_one()
        novel_id = int(novel.id)
        novel.status = "completed"
        db.commit()
    finally:
        db.close()

    created = anon_client.post(
        "/api/storyboards",
        json={
            "novel_id": novel_public_id,
            "target_episodes": 6,
            "target_episode_seconds": 90,
            "style_profile": "悬疑",
            "mode": "professional",
            "genre_style_key": "mystery_reversal",
            "director_style_key": "suspense_reversal",
            "auto_style_recommendation": True,
            "output_lanes": ["vertical_feed", "horizontal_cinematic"],
            "professional_mode": True,
            "audience_goal": "反转",
            "copyright_assertion": True,
        },
        headers=owner_headers,
    )
    assert created.status_code == 200
    project_id = created.json()["id"]
    assert created.json()["mode"] in {"quick", "professional"}
    assert isinstance(created.json()["style_recommendations"], list)

    db = SessionLocal()
    try:
        source_version = db.execute(
            select(NovelVersion).where(NovelVersion.novel_id == novel_id, NovelVersion.is_default == 1)
        ).scalar_one()
        source_version_id = int(source_version.id)
    finally:
        db.close()

    submit = anon_client.post(
        f"/api/storyboards/{project_id}/generate",
        json={"novel_version_id": source_version_id},
        headers=owner_headers,
    )
    assert submit.status_code == 200
    assert isinstance(submit.json()["task_id"], str) and len(submit.json()["task_id"]) >= 8
    assert len(submit.json()["created_version_ids"]) == 2

    versions = anon_client.get(f"/api/storyboards/{project_id}/versions", headers=owner_headers)
    assert versions.status_code == 200
    assert len(versions.json()) >= 2


def test_storyboard_style_recommendations_endpoint(anon_client):
    owner_headers = _auth_headers("story-style-owner", role="user")
    create_novel = anon_client.post(
        "/api/novels",
        json={"title": "深夜公寓的回声", "target_language": "zh", "genre": "悬疑"},
        headers=owner_headers,
    )
    assert create_novel.status_code == 200
    novel_public_id = create_novel.json()["id"]

    rec = anon_client.post(
        "/api/storyboards/style-recommendations",
        json={"novel_id": novel_public_id},
        headers=owner_headers,
    )
    assert rec.status_code == 200
    body = rec.json()
    assert body["novel_id"] == novel_public_id
    assert len(body["recommendations"]) >= 1


def test_storyboard_owner_scope(anon_client):
    owner_headers = _auth_headers("story-owner-a", role="user")
    other_headers = _auth_headers("story-owner-b", role="user")

    db = SessionLocal()
    try:
        novel = Novel(title="owner novel", target_language="zh", user_id="story-owner-a", status="completed")
        db.add(novel)
        db.commit()
        db.refresh(novel)

        project = StoryboardProject(
            novel_id=novel.id,
            owner_user_uuid="story-owner-a",
            status="draft",
            target_episodes=4,
            target_episode_seconds=90,
            output_lanes=["vertical_feed", "horizontal_cinematic"],
            active_lane="vertical_feed",
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        project_id = project.id
    finally:
        db.close()

    forbidden = anon_client.get(f"/api/storyboards/{project_id}/versions", headers=other_headers)
    assert forbidden.status_code == 403

    allowed = anon_client.get(f"/api/storyboards/{project_id}/versions", headers=owner_headers)
    assert allowed.status_code == 200


def test_storyboard_optimize_endpoint(anon_client):
    owner_headers = _auth_headers("story-opt-owner", role="user")
    db = SessionLocal()
    try:
        novel = Novel(title="opt novel", target_language="zh", user_id="story-opt-owner", status="completed")
        db.add(novel)
        db.commit()
        db.refresh(novel)
        project = StoryboardProject(
            novel_id=novel.id,
            owner_user_uuid="story-opt-owner",
            status="ready",
            target_episodes=2,
            target_episode_seconds=90,
            output_lanes=["vertical_feed"],
            active_lane="vertical_feed",
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        version = StoryboardVersion(
            storyboard_project_id=project.id,
            version_no=1,
            lane="vertical_feed",
            status="completed",
            is_default=1,
            is_final=0,
            quality_report_json={"rewrite_suggestions": ["补全 blocking/motivation/performance_note/continuity_anchor 四类导演字段。"]},
        )
        db.add(version)
        db.commit()
        db.refresh(version)
        db.add(
            StoryboardShot(
                storyboard_version_id=version.id,
                episode_no=1,
                scene_no=1,
                shot_no=1,
                duration_sec=6,
                action="test",
                dialogue="test",
                characters_json=["主角"],
            )
        )
        db.commit()
        project_id = project.id
        version_id = version.id
    finally:
        db.close()

    resp = anon_client.post(
        f"/api/storyboards/{project_id}/versions/{version_id}/optimize",
        headers=owner_headers,
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["optimized_shots"] >= 1


def test_character_profiles_and_prompt_gate(anon_client):
    owner_headers = _auth_headers("story-char-owner", role="user")
    db = SessionLocal()
    try:
        novel = Novel(title="char novel", target_language="zh", user_id="story-char-owner", status="completed")
        db.add(novel)
        db.commit()
        db.refresh(novel)
        novel_id = novel.id
        novel_uuid = novel.uuid or str(novel.id)

        db.add(
            StoryCharacterProfile(
                novel_id=novel_id,
                character_key="hero",
                display_name="主角",
                skin_tone="medium",
                ethnicity="east_asian",
                confidence=0.8,
            )
        )

        project = StoryboardProject(
            novel_id=novel.id,
            owner_user_uuid="story-char-owner",
            status="ready",
            target_episodes=2,
            target_episode_seconds=90,
            output_lanes=["vertical_feed"],
            active_lane="vertical_feed",
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        version = StoryboardVersion(
            storyboard_project_id=project.id,
            version_no=1,
            lane="vertical_feed",
            status="completed",
            is_default=1,
            is_final=0,
            quality_report_json={},
        )
        db.add(version)
        db.commit()
        db.refresh(version)
        db.add(
            StoryboardShot(
                storyboard_version_id=version.id,
                episode_no=1,
                scene_no=1,
                shot_no=1,
                duration_sec=3,
                action="主角冲进大厅",
                dialogue="我来了",
                characters_json=["主角"],
            )
        )
        db.commit()
        project_id = project.id
        version_id = version.id
    finally:
        db.close()

    rows = anon_client.get(f"/api/novels/{novel_uuid}/character-profiles", headers=owner_headers)
    assert rows.status_code == 200
    assert len(rows.json()) >= 1

    regen = anon_client.post(
        f"/api/storyboards/{project_id}/characters/generate?version_id={version_id}",
        headers=owner_headers,
    )
    assert regen.status_code == 200
    assert regen.json()["generated_count"] >= 1

    listed = anon_client.get(
        f"/api/storyboards/{project_id}/characters?version_id={version_id}",
        headers=owner_headers,
    )
    assert listed.status_code == 200
    assert len(listed.json()) >= 1

    # finalize should pass with generated prompts
    fin = anon_client.post(
        f"/api/storyboards/{project_id}/versions/{version_id}/finalize",
        headers=owner_headers,
    )
    assert fin.status_code == 200

    # export character prompts csv
    exported = anon_client.get(
        f"/api/storyboards/{project_id}/characters/export?version_id={version_id}&format=csv",
        headers=owner_headers,
    )
    assert exported.status_code == 200
    assert "character_key" in exported.text


def test_character_prompt_gate_blocks_when_identity_missing(anon_client):
    owner_headers = _auth_headers("story-char-missing-owner", role="user")
    db = SessionLocal()
    try:
        novel = Novel(title="char missing", target_language="zh", user_id="story-char-missing-owner", status="completed")
        db.add(novel)
        db.commit()
        db.refresh(novel)
        db.add(
            StoryCharacterProfile(
                novel_id=novel.id,
                character_key="hero-missing",
                display_name="缺失角色",
                skin_tone=None,
                ethnicity="east_asian",
                confidence=0.5,
            )
        )
        project = StoryboardProject(
            novel_id=novel.id,
            owner_user_uuid="story-char-missing-owner",
            status="ready",
            target_episodes=2,
            target_episode_seconds=90,
            output_lanes=["vertical_feed"],
            active_lane="vertical_feed",
        )
        db.add(project)
        db.commit()
        db.refresh(project)
        version = StoryboardVersion(
            storyboard_project_id=project.id,
            version_no=1,
            lane="vertical_feed",
            status="completed",
            is_default=1,
            is_final=0,
            quality_report_json={},
        )
        db.add(version)
        db.commit()
        db.refresh(version)
        db.add(
            StoryboardShot(
                storyboard_version_id=version.id,
                episode_no=1,
                scene_no=1,
                shot_no=1,
                duration_sec=3,
                action="缺失角色登场",
                dialogue="测试",
                characters_json=["缺失角色"],
            )
        )
        db.commit()
        project_id = project.id
        version_id = version.id
    finally:
        db.close()

    regen = anon_client.post(
        f"/api/storyboards/{project_id}/characters/generate?version_id={version_id}",
        headers=owner_headers,
    )
    assert regen.status_code == 200
    assert regen.json()["missing_identity_fields_count"] >= 1

    blocked = anon_client.post(
        f"/api/storyboards/{project_id}/versions/{version_id}/finalize",
        headers=owner_headers,
    )
    assert blocked.status_code == 409
