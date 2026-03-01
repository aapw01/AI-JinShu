from app.models.storyboard import StoryboardShot
from app.services.storyboard.adapter import (
    AdaptedChapter,
    build_director_intent,
    build_hard_constraints,
    build_platform_intent,
    extract_style_intent,
    prompt_contract,
)
from app.services.storyboard.exporter import export_shots_to_csv
from app.services.storyboard.scene_planner import decompose_scenes, partition_episodes
from app.services.storyboard.shot_planner import ShotDraft, expand_shots
from app.services.storyboard.validator import validate_storyboard


def test_partition_episodes_and_decompose_scenes():
    chapters = [
        AdaptedChapter(chapter_num=i, title=f"第{i}章 标题{i}", summary=f"摘要{i}", content=f"正文{i}")
        for i in range(1, 7)
    ]
    plans = partition_episodes(chapters, target_episodes=3)
    assert len(plans) == 3
    assert plans[0].episode_no == 1
    assert plans[0].chapter_refs

    vertical_scenes = decompose_scenes(plans[0], lane="vertical_feed")
    horizontal_scenes = decompose_scenes(plans[0], lane="horizontal_cinematic")
    assert len(vertical_scenes) == 3
    assert len(horizontal_scenes) == 4


def test_adapter_contract_and_constraints():
    chapters = [
        AdaptedChapter(chapter_num=1, title="危机初现", summary="主角遭遇危机与反转", content="正文A"),
        AdaptedChapter(chapter_num=2, title="真相揭开", summary="悬疑线推进", content="正文B"),
    ]
    style = extract_style_intent("悬疑", "热血爽文", "样本小说", chapters)
    director = build_director_intent(style, "vertical_feed")
    platform = build_platform_intent("vertical_feed", 90)
    constraints = build_hard_constraints(chapters)
    contract = prompt_contract(
        style_intent=style,
        director_intent=director,
        platform_intent=platform,
        hard_constraints=constraints,
    )

    assert contract["StyleIntent"]["genre"] == "悬疑"
    assert contract["DirectorIntent"]["pacing_goal"]
    assert contract["PlatformIntent"]["lane"] == "vertical_feed"
    assert contract["HardConstraints"]["forbidden_new_mainline"] is True


def test_validate_storyboard_quality_and_suggestions():
    shots = [
        ShotDraft(
            episode_no=1,
            scene_no=1,
            shot_no=i,
            location="主场景",
            time_of_day="夜",
            shot_size="特写",
            camera_angle="平",
            camera_move="静",
            duration_sec=2,
            characters_json=["主角"],
            action="冲突爆发并揭示危机",
            dialogue="你必须面对真相",
            emotion_beat="紧张",
            transition="切",
            sound_hint="鼓点",
            production_note="注意节奏",
            blocking="前压一步",
            motivation="反转",
            performance_note="强压情绪",
            continuity_anchor="承接上镜头视线方向",
        )
        for i in range(1, 10)
    ]
    result = validate_storyboard(
        shots=shots,
        lane="vertical_feed",
        target_episode_seconds=120,
        style_keywords=["冲突", "反转", "危机"],
    )
    assert 0 <= result.style_consistency_score <= 1
    assert result.completeness_rate > 0.9
    assert isinstance(result.hook_score_episode, dict)
    assert isinstance(result.rewrite_suggestions, list)


def test_expand_shots_generates_lane_specific_layout():
    chapter = AdaptedChapter(chapter_num=1, title="危机", summary="冲突", content="正文")
    episode = partition_episodes([chapter], target_episodes=1)[0]
    scene = decompose_scenes(episode, lane="vertical_feed")[0]
    style = extract_style_intent("悬疑", "热血", "样本", [chapter])
    vertical = expand_shots(
        episode=episode,
        scene=scene,
        lane="vertical_feed",
        platform=build_platform_intent("vertical_feed", 90),
        director=build_director_intent(style, "vertical_feed"),
    )
    assert len(vertical) == 3
    assert all(s.production_note for s in vertical)

    horizontal = expand_shots(
        episode=episode,
        scene=scene,
        lane="horizontal_cinematic",
        platform=build_platform_intent("horizontal_cinematic", 120),
        director=build_director_intent(style, "horizontal_cinematic"),
    )
    assert len(horizontal) == 4
    assert horizontal[0].shot_size == "全景"


def test_export_shots_to_csv_contains_headers_and_rows():
    shot = StoryboardShot(
        storyboard_version_id=1,
        episode_no=1,
        scene_no=1,
        shot_no=1,
        location="仓库",
        time_of_day="夜",
        shot_size="近景",
        camera_angle="平",
        camera_move="推",
        duration_sec=4,
        characters_json=["主角", "对手"],
        action="主角逼近对手",
        dialogue="把话说清楚。",
        emotion_beat="压迫",
        transition="切",
        sound_hint="低频轰鸣",
        production_note="注意前后景",
        blocking="主角向前一步",
        motivation="压迫",
        performance_note="克制怒意",
        continuity_anchor="接上一镜头左向右移动",
    )
    csv_text = export_shots_to_csv([shot])
    assert "episode_no,scene_no,shot_no" in csv_text
    assert "主角、对手" in csv_text
    assert "主角逼近对手" in csv_text
