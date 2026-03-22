"""LangGraph generation graph: construction, routing, and public entry points.

Graph is compiled once at module level (singleton) to avoid per-invocation overhead.
"""
import time

from langgraph.graph import END, StateGraph

from app.core.config import get_settings
from app.core.logging_config import log_event
from app.services.generation.common import MAX_RETRIES, REVIEW_SCORE_THRESHOLD, logger
from app.services.generation.nodes import (
    node_advance_chapter,
    node_beats,
    node_bridge_chapter,
    node_closure_gate,
    node_confirmation_gate,
    node_consistency_check,
    node_cross_chapter_check,
    node_final_book_review,
    node_finalize,
    node_init,
    node_load_context,
    node_outline,
    node_prewrite,
    node_review,
    node_revise,
    node_rollback_rerun,
    node_save_blocked,
    node_tail_rewrite,
    node_volume_replan,
    node_writer,
)
from app.services.generation.progress import is_volume_start, volume_no_for_chapter
from app.services.generation.state import GenerationState


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------

def _route_consistency(state: GenerationState) -> str:
    return "beats"  # always proceed; consistency issues injected into writer context


def _route_review(state: GenerationState) -> str:
    review_gate = state.get("review_gate") or {}
    if review_gate.get("decision") == "accept_with_minor_polish":
        return "finalizer"
    if state["score"] >= REVIEW_SCORE_THRESHOLD:
        return "finalizer"
    if state.get("review_attempt", 0) < MAX_RETRIES:
        return "revise"
    if state.get("rerun_count", 0) < 1:
        return "rollback_rerun"
    return "finalizer"


def _route_after_confirmation(state: GenerationState) -> str:
    if state["current_chapter"] > state["end_chapter"]:
        return "segment_done"
    return "volume_replan" if is_volume_start(state, state["current_chapter"]) else "load_context"


def _route_finalize(state: GenerationState) -> str:
    if state.get("quality_passed", True):
        return "advance_chapter"
    if state.get("rerun_count", 0) < 1:
        return "rollback_rerun"
    return "advance_chapter"


def _route_after_closure_gate(state: GenerationState) -> str:
    action = str((state.get("closure_state") or {}).get("action") or "")
    if action == "rewrite_tail":
        return "tail_rewrite"
    if action == "bridge_chapter":
        return "bridge_chapter"
    if state["current_chapter"] > state["end_chapter"]:
        return "segment_done"
    return "volume_replan" if is_volume_start(state, state["current_chapter"]) else "load_context"


def _route_after_tail_rewrite(state: GenerationState) -> str:
    if state["current_chapter"] > state["end_chapter"]:
        return "segment_done"
    return "volume_replan" if is_volume_start(state, state["current_chapter"]) else "load_context"


# ---------------------------------------------------------------------------
# Graph construction (singleton)
# ---------------------------------------------------------------------------

def _build_generation_graph():
    def _timed_node(name: str, fn):
        def _wrapped(state: GenerationState):
            started = time.perf_counter()
            chapter = int(state.get("current_chapter") or 0)
            task_id = state.get("task_id")
            novel_id = state.get("novel_id")
            log_event(
                logger,
                "pipeline.node.start",
                node=name,
                task_id=task_id,
                novel_id=novel_id,
                chapter_num=chapter,
                volume_no=volume_no_for_chapter(state, chapter) if chapter > 0 else None,
            )
            try:
                out = fn(state)
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                log_event(
                    logger,
                    "pipeline.node.end",
                    node=name,
                    task_id=task_id,
                    novel_id=novel_id,
                    chapter_num=chapter,
                    volume_no=volume_no_for_chapter(state, chapter) if chapter > 0 else None,
                    latency_ms=elapsed_ms,
                )
                slow_threshold_ms = int(get_settings().log_node_slow_threshold_ms or 2500)
                if elapsed_ms > slow_threshold_ms:
                    log_event(
                        logger,
                        "pipeline.node.slow",
                        level=30,
                        node=name,
                        task_id=task_id,
                        novel_id=novel_id,
                        chapter_num=chapter,
                        volume_no=volume_no_for_chapter(state, chapter) if chapter > 0 else None,
                        latency_ms=elapsed_ms,
                        threshold_ms=slow_threshold_ms,
                    )
                return out
            except Exception as exc:
                elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
                log_event(
                    logger,
                    "pipeline.node.error",
                    level=40,
                    message="Pipeline node failed",
                    node=name,
                    task_id=task_id,
                    novel_id=novel_id,
                    chapter_num=chapter,
                    volume_no=volume_no_for_chapter(state, chapter) if chapter > 0 else None,
                    latency_ms=elapsed_ms,
                    error_class=type(exc).__name__,
                    error_code="PIPELINE_NODE_ERROR",
                    error_category="permanent",
                )
                raise
        return _wrapped

    graph = StateGraph(GenerationState)
    graph.add_node("init", _timed_node("init", node_init))
    graph.add_node("prewrite", _timed_node("prewrite", node_prewrite))
    graph.add_node("outline", _timed_node("outline", node_outline))
    graph.add_node("confirmation_gate", _timed_node("confirmation_gate", node_confirmation_gate))
    graph.add_node("volume_replan", _timed_node("volume_replan", node_volume_replan))
    graph.add_node("load_context", _timed_node("load_context", node_load_context))
    graph.add_node("consistency_check", _timed_node("consistency_check", node_consistency_check))
    graph.add_node("save_blocked", _timed_node("save_blocked", node_save_blocked))
    graph.add_node("beats", _timed_node("beats", node_beats))
    graph.add_node("writer", _timed_node("writer", node_writer))
    graph.add_node("reviewer", _timed_node("reviewer", node_review))
    graph.add_node("cross_chapter_check", _timed_node("cross_chapter_check", node_cross_chapter_check))
    graph.add_node("revise", _timed_node("revise", node_revise))
    graph.add_node("rollback_rerun", _timed_node("rollback_rerun", node_rollback_rerun))
    graph.add_node("finalizer", _timed_node("finalizer", node_finalize))
    graph.add_node("advance_chapter", _timed_node("advance_chapter", node_advance_chapter))
    graph.add_node("closure_gate", _timed_node("closure_gate", node_closure_gate))
    graph.add_node("bridge_chapter", _timed_node("bridge_chapter", node_bridge_chapter))
    graph.add_node("tail_rewrite", _timed_node("tail_rewrite", node_tail_rewrite))
    graph.add_node("final_book_review", _timed_node("final_book_review", node_final_book_review))

    graph.set_entry_point("init")
    graph.add_edge("init", "prewrite")
    graph.add_edge("prewrite", "outline")
    graph.add_edge("outline", "confirmation_gate")
    graph.add_conditional_edges("confirmation_gate", _route_after_confirmation, {"volume_replan": "volume_replan", "load_context": "load_context", "segment_done": END})
    graph.add_edge("volume_replan", "load_context")
    graph.add_edge("load_context", "consistency_check")
    graph.add_conditional_edges("consistency_check", _route_consistency, {"save_blocked": "save_blocked", "beats": "beats"})
    graph.add_edge("beats", "writer")
    graph.add_edge("save_blocked", "advance_chapter")
    graph.add_edge("writer", "reviewer")
    graph.add_edge("reviewer", "cross_chapter_check")
    graph.add_conditional_edges("cross_chapter_check", _route_review, {"revise": "revise", "rollback_rerun": "rollback_rerun", "finalizer": "finalizer"})
    graph.add_edge("revise", "writer")
    graph.add_edge("rollback_rerun", "writer")
    graph.add_conditional_edges("finalizer", _route_finalize, {"rollback_rerun": "rollback_rerun", "advance_chapter": "advance_chapter"})
    graph.add_edge("advance_chapter", "closure_gate")
    graph.add_conditional_edges("closure_gate", _route_after_closure_gate, {"volume_replan": "volume_replan", "load_context": "load_context", "bridge_chapter": "bridge_chapter", "tail_rewrite": "tail_rewrite", "segment_done": END})
    graph.add_conditional_edges("bridge_chapter", _route_after_tail_rewrite, {"volume_replan": "volume_replan", "load_context": "load_context", "segment_done": END})
    graph.add_conditional_edges("tail_rewrite", _route_after_tail_rewrite, {"volume_replan": "volume_replan", "load_context": "load_context", "segment_done": END})
    graph.add_edge("final_book_review", END)
    return graph.compile()


_compiled_graph = _build_generation_graph()


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def run_generation_pipeline_langgraph(
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
    _compiled_graph.invoke(
        {
            "novel_id": novel_id,
            "novel_version_id": novel_version_id,
            "num_chapters": segment_target_chapters,
            "start_chapter": segment_start_chapter,
            "book_start_chapter": book_start_chapter,
            "book_target_total_chapters": book_target_total_chapters,
            "book_effective_end_chapter": book_effective_end_chapter,
            "segment_start_chapter": segment_start_chapter,
            "segment_target_chapters": segment_target_chapters,
            "segment_end_chapter": segment_start_chapter + segment_target_chapters - 1,
            "volume_no": volume_no,
            "task_id": task_id,
            "creation_task_id": creation_task_id,
            "progress_callback": progress_callback or (lambda *a, **k: None),
        }
    )


def run_final_book_review_only_langgraph(
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
    state = node_init(
        {
            "novel_id": novel_id,
            "novel_version_id": novel_version_id,
            "num_chapters": max(1, int(book_effective_end_chapter) - int(book_start_chapter) + 1),
            "start_chapter": book_start_chapter,
            "book_start_chapter": book_start_chapter,
            "book_target_total_chapters": book_target_total_chapters,
            "book_effective_end_chapter": book_effective_end_chapter,
            "segment_start_chapter": book_start_chapter,
            "segment_target_chapters": max(1, int(book_effective_end_chapter) - int(book_start_chapter) + 1),
            "segment_end_chapter": book_effective_end_chapter,
            "volume_no": volume_no,
            "task_id": task_id,
            "creation_task_id": creation_task_id,
            "progress_callback": progress_callback or (lambda *a, **k: None),
        }
    )
    state["current_chapter"] = int(book_effective_end_chapter) + 1
    node_final_book_review(state)
