from __future__ import annotations

from app.prompts import render_prompt, render_prompt_sections


def test_next_chapter_prompt_renders_tagged_json_context():
    prompt = render_prompt(
        "next_chapter",
        chapter_num=2,
        outline={
            "title": "火是沉默的证人",
            "outline": "主角与对手在机房对峙",
            "role": "主角, 对手",
            "purpose": "推进主线冲突",
            "suspense_level": "高",
            "foreshadowing": "钥匙",
            "payoff": "旧账",
            "hook": "断电倒计时",
            "mini_climax": "高",
        },
        context={
            "global_bible": "世界设定",
            "thread_ledger_text": "线索A",
            "recent_window": "上一章结尾",
            "retrieved_memory_brief": "历史事实: 林舟已经知道云家真正操盘者。",
            "retrieved_evidence": [
                {
                    "chapter_num": 1,
                    "source_type": "chapter_fact_delta",
                    "short_excerpt": "林舟在仓库里确认了云家的幕后指令。",
                }
            ],
            "prompt_contract": {"NarrativeIntent": {"goal": "推进冲突"}},
            "closure_state": {"phase_mode": "closing"},
        },
        language="zh",
        native_style_profile="都市现实",
    )
    assert "<prompt_contract_json>" in prompt
    assert '"NarrativeIntent"' in prompt
    assert "{'NarrativeIntent'" not in prompt
    assert "<global_bible>" in prompt
    assert "<outline_json>" in prompt
    assert "<retrieved_memory_brief>" in prompt
    assert "<retrieved_evidence_json>" in prompt


def test_rewrite_prompt_uses_annotations_json_tag():
    prompt = render_prompt(
        "rewrite_chapter_from_annotations",
        novel_title="测试小说",
        chapter_num=3,
        base_title="旧标题",
        target_language="zh",
        context_block="上一章摘要",
        annotations_json='[{"chapter_num":3,"issue_type":"style","instruction":"收紧节奏"}]',
        base_content="原正文",
        word_count=2600,
        min_word_count=2000,
        target_word_count=2600,
        soft_max_word_count=3000,
        hard_ceiling_word_count=3500,
        ideal_min_word_count=2200,
        ideal_max_word_count=2800,
    )
    assert "<annotations_json>" in prompt
    assert '"issue_type":"style"' in prompt
    assert "禁止输出章节标题行" in prompt
    assert "2000 到 3000 字" in prompt
    assert "3500" in prompt


def test_storyboard_character_prompt_has_structured_contract():
    prompt = render_prompt(
        "storyboard_character_master_prompt",
        lane="vertical_feed",
        style_profile="现实主义短剧",
        genre="都市",
        shot_context="E1S1#1 主角入场",
        character_json='{"display_name":"林砚"}',
    )
    assert '"master_prompt_text"' in prompt
    assert '"identity_lock"' in prompt
    assert '"negative_prompt_text"' in prompt
    assert '"camera_safe_notes"' in prompt


def test_render_prompt_sections_combines_policy_sections():
    prompt = render_prompt_sections(
        [
            "policy/memory_policy_fact_boundary",
            "policy/memory_policy_plan_vs_fact",
        ]
    )
    assert "只有正文中明确发生的事实" in prompt
    assert "不能直接当作已发生事实" in prompt
