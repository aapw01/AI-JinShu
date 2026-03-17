"""Unit tests for generic hard-constraint checks in consistency layer."""

from app.services.generation.consistency import (
    ConsistencyReport,
    _check_entity_hard_constraints,
    _check_progression_conflicts,
    _check_transition_conflicts,
)


def test_entity_forbidden_action_pattern_blocks_outline():
    report = ConsistencyReport()
    outline = {
        "title": "反击",
        "outline": "李青断臂后仍双手持剑冲锋，击退敌军。",
        "summary": "",
    }
    context = {
        "hard_constraints": {
            "entity_hard_constraints": [
                {
                    "entity": "李青",
                    "constraint_type": "forbidden_action_pattern",
                    "forbidden_patterns": ["双手持", "双臂发力"],
                }
            ]
        }
    }
    _check_entity_hard_constraints(report, outline, context, chapter_num=12)
    assert report.blockers
    assert any("双手持" in i.message for i in report.blockers)


def test_entity_forbidden_presence_blocks_outline():
    report = ConsistencyReport()
    outline = {
        "title": "归来",
        "outline": "王岳率兵出城，与主角并肩作战。",
        "summary": "",
    }
    context = {
        "hard_constraints": {
            "entity_hard_constraints": [
                {
                    "entity": "王岳",
                    "constraint_type": "forbidden_presence",
                }
            ]
        }
    }
    _check_entity_hard_constraints(report, outline, context, chapter_num=20)
    assert report.blockers
    assert any("不应在本章正常出场" in i.message for i in report.blockers)


def test_progression_conflict_blocks_already_revealed_information():
    report = ConsistencyReport()
    outline = {
        "title": "真相揭晓",
        "outline": "主角再次得知自己是云家嫡女。",
        "chapter_objective": "揭示主角真实身世",
        "required_new_information": ["主角是云家嫡女"],
        "relationship_delta": "",
    }
    context = {
        "anti_repeat_constraints": {
            "recent_objectives": ["确认主角真实身世"],
            "book_revealed_information": ["主角是云家嫡女"],
            "recent_relationship_deltas": [],
        }
    }
    _check_progression_conflicts(report, outline, context, chapter_num=18)
    assert report.blockers
    assert any("已在前文揭示" in issue.message for issue in report.blockers)


def test_transition_conflict_blocks_impossible_scene_reset():
    report = ConsistencyReport()
    outline = {
        "title": "卧室重逢",
        "outline": "主角回到卧室继续对峙。",
        "opening_scene": "主角卧室",
        "transition_mode": "direct",
    }
    context = {
        "previous_transition_state": {
            "ending_scene": "别墅门外",
            "last_action": "主角摔门而出",
            "scene_exit": "冲出别墅大门",
        }
    }
    _check_transition_conflicts(report, outline, context, chapter_num=19)
    assert report.blockers
    assert any("缺少过渡" in issue.message for issue in report.blockers)
