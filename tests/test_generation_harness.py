from __future__ import annotations

from app.core.database import SessionLocal
from app.models.novel import Novel, NovelMemory, StoryEntity, StoryFact, StoryForeshadow, StoryRelation
from app.services.generation.harness.consistency_eval import run_consistency_eval_cases
from app.services.generation.harness.context_budget import run_context_budget_harness
from app.services.generation.harness.fact_ledger import build_fact_ledger
from app.services.generation.harness.replay import build_chapter_replay_bundle


def test_build_chapter_replay_bundle_reads_runtime_snapshot():
    runtime_state = {
        "chapter_runtime_snapshots": {
            "3": {
                "chapter_num": 3,
                "stage": "writer",
                "prompt": {
                    "prompt_asset_id": "generation.chapter.writer",
                    "prompt_hash": "abc123",
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                },
                "context": {
                    "included_block_ids": ["outline_contract", "character_focus_pack"],
                    "dropped_block_ids": ["knowledge_chunks"],
                },
                "diagnostics": {"selected_variant": "A"},
            }
        }
    }

    bundle = build_chapter_replay_bundle(runtime_state, chapter_num=3)

    assert bundle["replay_status"] == "available"
    assert bundle["chapter_num"] == 3
    assert bundle["prompt"]["prompt_asset_id"] == "generation.chapter.writer"
    assert bundle["context"]["included_block_ids"] == ["outline_contract", "character_focus_pack"]
    assert bundle["diagnostics"]["selected_variant"] == "A"


def test_build_chapter_replay_bundle_reports_missing_snapshot():
    bundle = build_chapter_replay_bundle({"chapter_runtime_snapshots": {}}, chapter_num=8)

    assert bundle["replay_status"] == "missing_snapshot"
    assert bundle["chapter_num"] == 8
    assert bundle["prompt"] == {}


def test_run_consistency_eval_cases_scores_expected_blocker():
    cases = [
        {
            "id": "dead-character-outline",
            "report": {"blockers": 1, "warnings": 0},
            "expected": {"min_blockers": 1, "max_warnings": 0},
        },
        {
            "id": "clean-outline",
            "report": {"blockers": 0, "warnings": 0},
            "expected": {"max_blockers": 0, "max_warnings": 0},
        },
    ]

    result = run_consistency_eval_cases(cases)

    assert result["total"] == 2
    assert result["passed"] == 2
    assert result["failed"] == 0
    assert result["pass_rate"] == 1.0
    assert result["case_results"][0]["passed"] is True


def test_run_consistency_eval_cases_surfaces_failures():
    cases = [
        {
            "id": "missed-dead-character",
            "report": {"blockers": 0, "warnings": 0},
            "expected": {"min_blockers": 1},
        }
    ]

    result = run_consistency_eval_cases(cases)

    assert result["passed"] == 0
    assert result["failed"] == 1
    assert result["case_results"][0]["passed"] is False
    assert "blockers" in result["case_results"][0]["reasons"][0]


def test_run_consistency_eval_cases_rejects_missing_report_metrics():
    cases = [
        {
            "id": "typo-in-warning-metric",
            "report": {"blockers": 0, "warnings": 0},
            "expected": {"max_warning": 0},
        }
    ]

    result = run_consistency_eval_cases(cases)

    assert result["passed"] == 0
    assert result["failed"] == 1
    assert result["case_results"][0]["passed"] is False
    assert "missing report metric" in result["case_results"][0]["reasons"][0]


def test_build_fact_ledger_groups_entities_facts_and_foreshadows():
    db = SessionLocal()
    try:
        novel = Novel(title="事实账本测试", target_language="zh")
        db.add(novel)
        db.commit()
        db.refresh(novel)
        novel_id = int(novel.id)

        entity = StoryEntity(
            novel_id=novel_id,
            entity_type="character",
            name="林秋",
            status="alive",
            summary="主角",
        )
        db.add(entity)
        db.flush()
        db.add(
            StoryFact(
                novel_id=novel_id,
                entity_id=int(entity.id),
                fact_type="location",
                value_json={"value": "旧宅"},
                chapter_from=2,
            )
        )
        db.add(
            StoryForeshadow(
                novel_id=novel_id,
                foreshadow_id="fs-1",
                title="旧账册",
                planted_chapter=1,
                state="planted",
            )
        )
        db.add(
            StoryRelation(
                novel_id=novel_id,
                source="林秋",
                target="陆沉",
                relation_type="ally",
                description="暂时结盟",
                chapter_num=2,
            )
        )
        db.add(
            NovelMemory(
                novel_id=novel_id,
                memory_type="character",
                key="林秋",
                content={"status": "alive", "location": "旧宅门外", "chapter_num": 2},
            )
        )
        db.commit()

        ledger = build_fact_ledger(db, novel_id=novel_id, novel_version_id=None)
    finally:
        db.close()

    assert ledger["entities"]["character"][0]["name"] == "林秋"
    assert ledger["facts"][0]["entity_name"] == "林秋"
    assert ledger["facts_by_entity"]["林秋"][0]["fact_type"] == "location"
    assert ledger["foreshadows"][0]["foreshadow_id"] == "fs-1"
    assert ledger["relations"][0]["source"] == "林秋"
    assert ledger["character_memory"]["林秋"]["location"] == "旧宅门外"
    assert ledger["ledger_meta"]["entity_count"] == 1


def test_run_context_budget_harness_keeps_required_and_character_focus():
    scenarios = [
        {
            "id": "writer-budget-pressure",
            "token_budget": 120,
            "blocks": [
                {"block_id": "outline_contract", "source_type": "outline_contract", "tier": "required", "priority": 1, "approx_tokens": 70, "value": {"goal": "推进"}},
                {"block_id": "character_focus_pack", "source_type": "character_focus_pack", "tier": "preferred", "priority": 2, "approx_tokens": 40, "value": {"characters": [{"name": "林秋"}]}},
                {"block_id": "knowledge_chunks", "source_type": "knowledge_chunks", "tier": "optional", "priority": 10, "approx_tokens": 40, "value": [{"content": "资料"}]},
            ],
            "must_include": ["outline_contract", "character_focus_pack"],
            "must_drop": ["knowledge_chunks"],
        }
    ]

    result = run_context_budget_harness(scenarios)

    assert result["total"] == 1
    assert result["passed"] == 1
    assert result["scenario_results"][0]["passed"] is True
    assert result["scenario_results"][0]["included_block_ids"] == ["outline_contract", "character_focus_pack"]


def test_run_context_budget_harness_requires_must_drop_to_be_dropped():
    scenarios = [
        {
            "id": "misspelled-drop-id",
            "token_budget": 100,
            "blocks": [
                {"block_id": "outline_contract", "source_type": "outline_contract", "tier": "required", "priority": 1, "approx_tokens": 20, "value": {"goal": "推进"}},
            ],
            "must_drop": ["knowledge_chunks"],
        }
    ]

    result = run_context_budget_harness(scenarios)

    assert result["passed"] == 0
    assert result["failed"] == 1
    assert result["scenario_results"][0]["passed"] is False
    assert "not dropped" in result["scenario_results"][0]["reasons"][0]
