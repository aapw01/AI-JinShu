"""Generation pipeline entry point (LangGraph-only)."""


def run_generation_pipeline(
    novel_id: int,
    novel_version_id: int,
    segment_target_chapters: int,
    segment_start_chapter: int,
    book_start_chapter: int,
    book_target_total_chapters: int,
    book_effective_end_chapter: int,
    volume_no: int,
    progress_callback=None,
    task_id: str | None = None,
    creation_task_id: int | None = None,
) -> None:
    """Public entry — delegates to LangGraph orchestration."""
    from app.services.generation.langgraph_pipeline import run_generation_pipeline_langgraph

    run_generation_pipeline_langgraph(
        novel_id=novel_id,
        novel_version_id=novel_version_id,
        segment_target_chapters=segment_target_chapters,
        segment_start_chapter=segment_start_chapter,
        book_start_chapter=book_start_chapter,
        book_target_total_chapters=book_target_total_chapters,
        book_effective_end_chapter=book_effective_end_chapter,
        volume_no=volume_no,
        progress_callback=progress_callback,
        task_id=task_id,
        creation_task_id=creation_task_id,
    )


def run_final_book_review_only(
    novel_id: int,
    novel_version_id: int,
    book_start_chapter: int,
    book_target_total_chapters: int,
    book_effective_end_chapter: int,
    volume_no: int,
    progress_callback=None,
    task_id: str | None = None,
    creation_task_id: int | None = None,
) -> None:
    """Resume directly from final book review when chapter writing is already complete."""
    from app.services.generation.langgraph_pipeline import run_final_book_review_only_langgraph

    run_final_book_review_only_langgraph(
        novel_id=novel_id,
        novel_version_id=novel_version_id,
        book_start_chapter=book_start_chapter,
        book_target_total_chapters=book_target_total_chapters,
        book_effective_end_chapter=book_effective_end_chapter,
        volume_no=volume_no,
        progress_callback=progress_callback,
        task_id=task_id,
        creation_task_id=creation_task_id,
    )
