"""Generation pipeline entry point (LangGraph-only)."""


def run_generation_pipeline(
    novel_id: int,
    num_chapters: int,
    start_chapter: int,
    progress_callback=None,
    task_id: str | None = None,
) -> None:
    """Public entry — delegates to LangGraph orchestration."""
    from app.services.generation.langgraph_pipeline import run_generation_pipeline_langgraph

    run_generation_pipeline_langgraph(
        novel_id=novel_id,
        num_chapters=num_chapters,
        start_chapter=start_chapter,
        progress_callback=progress_callback,
        task_id=task_id,
    )
