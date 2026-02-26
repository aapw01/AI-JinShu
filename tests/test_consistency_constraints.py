"""Unit tests for generic hard-constraint checks in consistency layer."""

from app.services.generation.consistency import (
    ConsistencyReport,
    _check_entity_hard_constraints,
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

