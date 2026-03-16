"""GenerationState type definition for the LangGraph generation pipeline."""
from typing import Any, Callable, TypedDict

from app.services.generation.agents import (
    FactExtractorAgent,
    FinalizerAgent,
    FinalReviewerAgent,
    OutlinerAgent,
    PrewritePlannerAgent,
    ReviewerAgent,
    WriterAgent,
)
from app.services.memory.character_state import CharacterStateManager
from app.services.memory.story_bible import CheckpointStore, QualityReportStore, StoryBibleStore
from app.services.memory.summary_manager import SummaryManager


class GenerationState(TypedDict, total=False):
    novel_id: int
    novel_version_id: int
    book_start_chapter: int
    book_target_total_chapters: int
    book_effective_end_chapter: int
    book_min_total_chapters: int
    book_max_total_chapters: int
    segment_start_chapter: int
    segment_target_chapters: int
    segment_end_chapter: int
    next_chapter: int
    num_chapters: int
    target_chapters: int
    min_total_chapters: int
    max_total_chapters: int
    start_chapter: int
    current_chapter: int
    end_chapter: int
    task_id: str | None
    creation_task_id: int | None
    progress_callback: Callable[..., None]
    strategy: str
    target_language: str
    native_style_profile: str
    novel_info: dict[str, Any]
    prewrite: dict[str, Any]
    full_outlines: list[dict[str, Any]]
    summary_mgr: SummaryManager
    char_mgr: CharacterStateManager
    prewrite_agent: PrewritePlannerAgent
    outliner: OutlinerAgent
    writer: WriterAgent
    reviewer: ReviewerAgent
    finalizer: FinalizerAgent
    final_reviewer: FinalReviewerAgent
    fact_extractor: FactExtractorAgent
    outline: dict[str, Any]
    context: dict[str, Any]
    consistency_report: Any
    draft: str
    candidate_drafts: list[dict[str, Any]]
    feedback: str
    factual_feedback: str
    aesthetic_feedback: str
    score: float
    factual_score: float
    aesthetic_review_score: float
    review_attempt: int
    rerun_count: int
    chapter_token_snapshot: dict[str, int]
    total_input_tokens: int
    total_output_tokens: int
    estimated_cost: float
    volume_size: int
    bible_store: StoryBibleStore
    checkpoint_store: CheckpointStore
    quality_store: QualityReportStore
    quality_passed: bool
    volume_no: int
    volume_plan: dict[str, Any]
    segment_plan: dict[str, Any]
    decision_state: dict[str, Any]
    closure_state: dict[str, Any]
    retry_resume_chapter: int
    consistency_soft_fail: bool
    tail_rewrite_attempts: int
    bridge_attempts: int
    low_progress_streak: int
    pacing_mode: str
    review_suggestions: dict[str, Any]
    consistency_scorecard: dict[str, Any]
    review_gate: dict[str, Any]
