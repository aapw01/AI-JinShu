"""Unit tests for generic hard-constraint checks in consistency layer."""

from app.services.generation.consistency import (
    ConsistencyReport,
    _check_entity_hard_constraints,
    _check_progression_conflicts,
    _check_transition_conflicts,
    _payoff_relates_to_planted,
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


# ---------------------------------------------------------------------------
# _payoff_relates_to_planted: 多信号匹配
# 旧实现只有 substring，措辞稍改就误报；以下用例覆盖三种正向信号 + 一种反例。
# ---------------------------------------------------------------------------

def test_payoff_matches_planted_when_substring_overlaps():
    assert _payoff_relates_to_planted(
        payoff="主角揭穿黑石令的真正主人",
        planted="黑石令的真正主人在朝堂之内",
    )


def test_payoff_matches_planted_when_id_token_shared():
    assert _payoff_relates_to_planted(
        payoff="终于回收 F-007，主角拿到铁券",
        planted="F-007: 主角在第3章捡到的半枚铁券",
    )


def test_payoff_matches_planted_via_keyword_jaccard():
    # 措辞完全不同，但人物 + 关键道具 + 关键动作三者重叠，应被视为关联。
    assert _payoff_relates_to_planted(
        payoff="林霜在玉清宫将断玉拼合，揭示母亲遗书的下落",
        planted="林霜母亲留下的断玉与遗书藏在玉清宫深处",
    )


def test_payoff_does_not_match_unrelated_planted():
    # 完全无关的两件事，不应被多信号匹配误判为有关。
    assert not _payoff_relates_to_planted(
        payoff="主角与北疆使团达成秘密盟约",
        planted="厨房里的灶神像在第二章被打碎",
    )
