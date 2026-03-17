from __future__ import annotations

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.novel import Novel, NovelMemory, NovelMemoryRevision
from app.services.memory.progression_control import (
    ProgressionPromotionService,
    ProgressionRollbackService,
)
from app.services.memory.progression_state import ProgressionMemoryManager


def _create_novel(title: str = "progression-control-test") -> Novel:
    db = SessionLocal()
    try:
        novel = Novel(title=title, target_language="zh")
        db.add(novel)
        db.commit()
        db.refresh(novel)
        return novel
    finally:
        db.close()


def test_progression_promotion_blocks_low_confidence_and_review_findings():
    service = ProgressionPromotionService()

    blocked = service.decide(
        chapter_num=12,
        extraction={
            "advancement": {
                "chapter_objective": "推进调查",
                "actual_progress": "主角继续调查",
                "new_information": ["发现新线索"],
            },
            "transition": {
                "ending_scene": "别墅门外",
                "last_action": "主角摔门而出",
                "scene_exit": "冲出大门",
            },
            "advancement_confidence": 0.6,
            "transition_confidence": 0.9,
            "validation_notes": ["线索描述较模糊"],
        },
        outline_contract={"chapter_objective": "推进调查"},
        review_suggestions={
            "scorecards": {
                "progression": {
                    "no_new_delta": ["本章没有新增不可逆变化"],
                    "transition_conflict": ["上一章已离场，本章开头回到卧室"],
                }
            }
        },
        review_gate={},
    )

    assert blocked["decision"] == "promote_none"
    assert blocked["promoted_payload"]["advancement"] == {}
    assert blocked["promoted_payload"]["transition"] == {}
    assert "advancement_low_confidence" in blocked["blocked_reasons"]
    assert "transition_blocked_by_review_conflict" in blocked["blocked_reasons"]

    allowed = service.decide(
        chapter_num=13,
        extraction={
            "advancement": {
                "chapter_objective": "推进主线冲突",
                "actual_progress": "主角公开质问反派",
                "new_information": ["反派与祭坛有关"],
                "irreversible_change": "主角与盟友正式决裂",
            },
            "transition": {
                "ending_scene": "祭坛入口",
                "last_action": "主角踏入入口",
                "scene_exit": "进入祭坛通道",
            },
            "advancement_confidence": 0.9,
            "transition_confidence": 0.91,
        },
        outline_contract={"chapter_objective": "推进主线冲突"},
        review_suggestions={"scorecards": {"progression": {}}},
        review_gate={},
    )

    assert allowed["decision"] == "promote_all"
    assert allowed["promoted_payload"]["advancement"]["actual_progress"] == "主角公开质问反派"
    assert allowed["promoted_payload"]["transition"]["ending_scene"] == "祭坛入口"


def test_progression_promotion_blocks_advancement_that_conflicts_with_outline_contract():
    service = ProgressionPromotionService()

    result = service.decide(
        chapter_num=18,
        extraction={
            "advancement": {
                "chapter_objective": "主角与家人温情和解",
                "actual_progress": "主角回家与哥哥们温情团聚",
                "new_information": ["哥哥准备了庆功宴"],
                "irreversible_change": "家庭关系正式缓和",
                "conflict_axis": "家族温情",
                "payoff_kind": "relationship_shift",
                "reveal_kind": "relationship",
            },
            "transition": {
                "ending_scene": "餐厅门口",
                "last_action": "主角推门进入餐厅",
                "scene_exit": "进入室内",
            },
            "advancement_confidence": 0.92,
            "transition_confidence": 0.9,
        },
        outline_contract={
            "chapter_objective": "推进祭坛调查并揭示幕后组织线索",
            "required_new_information": ["祭坛背后的组织线索"],
            "conflict_axis": "祭坛调查",
            "payoff_kind": "investigation",
            "reveal_kind": "supernatural",
        },
        review_suggestions={"scorecards": {"progression": {}}},
        review_gate={},
    )

    assert result["decision"] == "promote_transition_only"
    assert result["promoted_payload"]["advancement"] == {}
    assert result["promoted_payload"]["transition"]["ending_scene"] == "餐厅门口"
    assert "advancement_outline_objective_mismatch" in result["blocked_reasons"]


def test_progression_memory_upsert_writes_revision_audit():
    novel = _create_novel("progression-revision-audit")
    mgr = ProgressionMemoryManager()

    mgr.save_chapter_advancement(
        novel.id,
        3,
        {"chapter_objective": "旧目标"},
        volume_no=1,
        promotion_score=0.83,
    )
    mgr.save_chapter_advancement(
        novel.id,
        3,
        {"chapter_objective": "新目标", "actual_progress": "推进了新变化"},
        volume_no=1,
        promotion_score=0.91,
    )

    db = SessionLocal()
    try:
        revisions = db.execute(
            select(NovelMemoryRevision)
            .where(
                NovelMemoryRevision.novel_id == novel.id,
                NovelMemoryRevision.memory_type == "chapter_advancement",
                NovelMemoryRevision.memory_key == "3",
            )
            .order_by(NovelMemoryRevision.id.asc())
        ).scalars().all()
        assert len(revisions) == 2
        assert revisions[0].action == "upsert"
        assert revisions[0].old_content == {}
        assert revisions[0].new_content["chapter_objective"] == "旧目标"
        assert revisions[1].old_content["chapter_objective"] == "旧目标"
        assert revisions[1].new_content["chapter_objective"] == "新目标"
        assert revisions[1].promotion_score == 0.91
    finally:
        db.close()


def test_progression_rollback_removes_future_memory_and_rebuilds_derived_state():
    novel = _create_novel("progression-rollback-rebuild")
    mgr = ProgressionMemoryManager()
    db = SessionLocal()
    try:
        mgr.save_chapter_advancement(
            novel.id,
            1,
            {
                "chapter_objective": "揭示身世",
                "actual_progress": "主角确认自己并非林家亲女儿",
                "new_information": ["主角是云家嫡女"],
                "conflict_axis": "身世真相",
                "payoff_kind": "truth_reveal",
                "reveal_kind": "identity",
                "forbidden_repeats": ["不要再次把身世揭晓当成本章主推进"],
            },
            volume_no=1,
            promotion_score=0.9,
            db=db,
        )
        mgr.save_chapter_transition(
            novel.id,
            1,
            {
                "ending_scene": "别墅门外",
                "last_action": "主角摔门而出",
                "scene_exit": "冲出大门",
            },
            volume_no=1,
            promotion_score=0.9,
            db=db,
        )
        mgr.merge_volume_arc_state(
            novel.id,
            1,
            1,
            {
                "chapter_objective": "揭示身世",
                "new_information": ["主角是云家嫡女"],
                "conflict_axis": "身世真相",
                "payoff_kind": "truth_reveal",
                "reveal_kind": "identity",
                "forbidden_repeats": ["不要再次把身世揭晓当成本章主推进"],
            },
            promotion_score=0.9,
            db=db,
        )
        mgr.merge_book_progression_state(
            novel.id,
            1,
            {
                "new_information": ["主角是云家嫡女"],
                "major_beats": ["认亲"],
                "payoff_kind": "truth_reveal",
                "reveal_kind": "identity",
                "forbidden_repeats": ["不要再次把身世揭晓当成本章主推进"],
                "conflict_axis": "身世真相",
            },
            promotion_score=0.9,
            db=db,
        )

        mgr.save_chapter_advancement(
            novel.id,
            2,
            {
                "chapter_objective": "错误推进",
                "actual_progress": "错误地认定反派已经死亡",
                "new_information": ["反派已死"],
                "conflict_axis": "错误情报",
                "payoff_kind": "truth_reveal",
                "reveal_kind": "truth",
                "forbidden_repeats": ["不要重复死亡判定"],
            },
            volume_no=1,
            promotion_score=0.88,
            db=db,
        )
        mgr.save_chapter_transition(
            novel.id,
            2,
            {
                "ending_scene": "卧室",
                "last_action": "主角坐回床边",
                "scene_exit": "留在卧室",
            },
            volume_no=1,
            promotion_score=0.88,
            db=db,
        )
        mgr.merge_volume_arc_state(
            novel.id,
            1,
            2,
            {
                "chapter_objective": "错误推进",
                "new_information": ["反派已死"],
                "conflict_axis": "错误情报",
                "payoff_kind": "truth_reveal",
                "reveal_kind": "truth",
                "forbidden_repeats": ["不要重复死亡判定"],
            },
            promotion_score=0.88,
            db=db,
        )
        mgr.merge_book_progression_state(
            novel.id,
            2,
            {
                "new_information": ["反派已死"],
                "major_beats": ["误判死亡"],
                "payoff_kind": "truth_reveal",
                "reveal_kind": "truth",
                "forbidden_repeats": ["不要重复死亡判定"],
                "conflict_axis": "错误情报",
            },
            promotion_score=0.88,
            db=db,
        )
        db.commit()

        result = ProgressionRollbackService(mgr).rollback_from_chapter(
            novel_id=novel.id,
            novel_version_id=None,
            from_chapter=2,
            db=db,
        )
        db.commit()

        chapter_memories = db.execute(
            select(NovelMemory)
            .where(
                NovelMemory.novel_id == novel.id,
                NovelMemory.memory_type == "chapter_advancement",
            )
            .order_by(NovelMemory.key.asc())
        ).scalars().all()
        assert [row.key for row in chapter_memories] == ["1"]

        transition_memories = db.execute(
            select(NovelMemory)
            .where(
                NovelMemory.novel_id == novel.id,
                NovelMemory.memory_type == "chapter_transition",
            )
            .order_by(NovelMemory.key.asc())
        ).scalars().all()
        assert [row.key for row in transition_memories] == ["1"]

        volume_state = db.execute(
            select(NovelMemory).where(
                NovelMemory.novel_id == novel.id,
                NovelMemory.memory_type == "volume_arc_state",
                NovelMemory.key == "volume:1",
            )
        ).scalar_one()
        assert "反派已死" not in str(volume_state.content)
        assert "主角是云家嫡女" in str(volume_state.content)

        book_state = db.execute(
            select(NovelMemory).where(
                NovelMemory.novel_id == novel.id,
                NovelMemory.memory_type == "book_progression",
                NovelMemory.key == "book",
            )
        ).scalar_one()
        assert "反派已死" not in str(book_state.content)
        assert "主角是云家嫡女" in str(book_state.content)

        revisions = db.execute(
            select(NovelMemoryRevision)
            .where(NovelMemoryRevision.novel_id == novel.id)
            .order_by(NovelMemoryRevision.id.asc())
        ).scalars().all()
        assert any(row.action == "rollback" and row.memory_type == "chapter_advancement" for row in revisions)
        assert any(row.action == "rebuild" and row.memory_type == "volume_arc_state" for row in revisions)
        assert result["deleted_advancement"] == 1
        assert result["deleted_transition"] == 1
    finally:
        db.close()
