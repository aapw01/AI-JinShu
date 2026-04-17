"""长篇生成 LangGraph 工作流定义。

模块职责：
- 定义节点、条件路由、回路与公共入口。
- 把长篇生成拆成 `init -> prewrite -> outline -> writer -> review -> finalize`
  以及桥接章、尾章重写、终审等补充分支。

系统位置：
- 上游是 Celery generation task。
- 下游是各个 generation node 的实际业务逻辑。

面试可讲点：
- 当前项目为什么选择 LangGraph：重点在有状态、多分支、可回路流程编排。
- 当前项目没有直接用 LangGraph 官方 checkpointer，而是业务侧自己做章节 checkpoint。
- 为什么图在模块级 compile 一次，而不是每次调用都重建。
"""
import time

from langgraph.graph import END, StateGraph

from app.core.config import get_settings
from app.core.logging_config import log_event
from app.core.strategy import get_max_retries
from app.services.generation.common import REVIEW_SCORE_THRESHOLD, logger
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
    node_quality_rewrite_init,
    node_init,
    node_load_context,
    node_outline,
    node_prewrite,
    node_refine_chapter_outline,
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
    """一致性检查后的路由。

    当前策略选择始终继续到 beats，把一致性问题以约束形式注入后续写作，
    而不是在这里直接中断整条链路。
    """
    return "beats"  # always proceed; consistency issues injected into writer context


def _route_review(state: GenerationState) -> str:
    """为审校选择路由。"""
    review_gate = state.get("review_gate") or {}
    if review_gate.get("decision") == "accept_with_minor_polish":
        return "finalizer"
    if state["score"] >= REVIEW_SCORE_THRESHOLD:
        return "finalizer"
    max_retries = get_max_retries(state.get("strategy"))
    if state.get("review_attempt", 0) < max_retries:
        return "revise"
    if state.get("rerun_count", 0) < 1:
        return "rollback_rerun"
    return "finalizer"


def _route_after_confirmation(state: GenerationState) -> str:
    """确认起始状态后决定进入分卷重规划还是直接加载上下文。"""
    if state["current_chapter"] > state["end_chapter"]:
        return "segment_done"
    return "volume_replan" if is_volume_start(state, state["current_chapter"]) else "load_context"


def _route_finalize(state: GenerationState) -> str:
    """finalizer 之后决定直接推进章节，还是回滚重跑一次。"""
    if state.get("quality_passed", True):
        return "advance_chapter"
    max_retries = get_max_retries(state.get("strategy"))
    if state.get("rerun_count", 0) < 1 and max_retries > 0:
        return "rollback_rerun"
    return "advance_chapter"


def _route_after_closure_gate(state: GenerationState) -> str:
    """卷末收束判断后，决定尾章重写、桥接章还是进入下一段。"""
    action = str((state.get("closure_state") or {}).get("action") or "")
    if action == "rewrite_tail":
        return "tail_rewrite"
    if action == "bridge_chapter":
        return "bridge_chapter"
    if state["current_chapter"] > state["end_chapter"]:
        return "segment_done"
    return "volume_replan" if is_volume_start(state, state["current_chapter"]) else "load_context"


def _route_after_tail_rewrite(state: GenerationState) -> str:
    """尾章重写或桥接章完成后，回到正常章节推进流程。"""
    if state["current_chapter"] > state["end_chapter"]:
        return "segment_done"
    return "volume_replan" if is_volume_start(state, state["current_chapter"]) else "load_context"


def _route_after_final_review(state: GenerationState) -> str:
    """整书终审后，决定是否进入质量补写模式。"""
    blocked = state.get("quality_blocked_chapters") or []
    if blocked:
        return "quality_rewrite_init"
    return "done"


def _route_after_advance_chapter(state: GenerationState) -> str:
    """章节推进后的公共出口。

    正常模式下会进入 `closure_gate` 判断卷末节奏；如果整书终审已经标记出
    被阻塞章节，则改走质量重写回路。
    """
    # If in quality-rewrite mode (key set by final_book_review), route accordingly
    blocked = state.get("quality_blocked_chapters")
    if blocked is not None:
        if blocked:
            return "quality_rewrite_init"
        return "done"
    # Normal flow
    return "closure_gate"


# ---------------------------------------------------------------------------
# Graph construction (singleton)
# ---------------------------------------------------------------------------

def _build_generation_graph():
    """构建并编译整条生成工作流图。

    这里把每个节点都包了一层 `_timed_node`，不是为了“好看”，而是为了
    把节点级耗时、异常、慢调用日志统一打平到同一套事件模型里。
    """
    def _timed_node(name: str, fn):
        """为节点函数包上一层统一的开始/结束/慢调用/异常日志。"""
        def _wrapped(state: GenerationState):
            """执行单个图节点，并把节点级观测事件写入日志系统。"""
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
    graph.add_node("refine_outline", _timed_node("refine_outline", node_refine_chapter_outline))
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
    graph.add_node("quality_rewrite_init", _timed_node("quality_rewrite_init", node_quality_rewrite_init))

    graph.set_entry_point("init")
    graph.add_edge("init", "prewrite")
    graph.add_edge("prewrite", "outline")
    graph.add_edge("outline", "confirmation_gate")
    graph.add_conditional_edges("confirmation_gate", _route_after_confirmation, {"volume_replan": "volume_replan", "load_context": "load_context", "segment_done": END})
    graph.add_edge("volume_replan", "load_context")
    graph.add_edge("load_context", "refine_outline")
    graph.add_edge("refine_outline", "consistency_check")
    graph.add_conditional_edges("consistency_check", _route_consistency, {"save_blocked": "save_blocked", "beats": "beats"})
    graph.add_edge("beats", "writer")
    graph.add_edge("save_blocked", "advance_chapter")
    graph.add_edge("writer", "reviewer")
    graph.add_edge("reviewer", "cross_chapter_check")
    graph.add_conditional_edges("cross_chapter_check", _route_review, {"revise": "revise", "rollback_rerun": "rollback_rerun", "finalizer": "finalizer"})
    graph.add_edge("revise", "writer")
    graph.add_edge("rollback_rerun", "writer")
    graph.add_conditional_edges("finalizer", _route_finalize, {"rollback_rerun": "rollback_rerun", "advance_chapter": "advance_chapter"})
    graph.add_conditional_edges(
        "advance_chapter",
        _route_after_advance_chapter,
        {"closure_gate": "closure_gate", "quality_rewrite_init": "quality_rewrite_init", "done": END},
    )
    graph.add_conditional_edges("closure_gate", _route_after_closure_gate, {"volume_replan": "volume_replan", "load_context": "load_context", "bridge_chapter": "bridge_chapter", "tail_rewrite": "tail_rewrite", "segment_done": END})
    graph.add_conditional_edges("bridge_chapter", _route_after_tail_rewrite, {"volume_replan": "volume_replan", "load_context": "load_context", "segment_done": END})
    graph.add_conditional_edges("tail_rewrite", _route_after_tail_rewrite, {"volume_replan": "volume_replan", "load_context": "load_context", "segment_done": END})
    graph.add_conditional_edges(
        "final_book_review",
        _route_after_final_review,
        {"quality_rewrite_init": "quality_rewrite_init", "done": END},
    )
    graph.add_edge("quality_rewrite_init", "load_context")
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
    """运行一段章节生成流程。

    注意这里传入的是“当前 segment”的边界，而不是整本书所有章节。
    当前项目采用按卷/按段渐进式生成，因此图每次只负责一段可恢复窗口。
    """
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
    """仅运行整书终审，不再继续写新章节。"""
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

    # Log quality-blocked chapters found during standalone final review
    q_blocked = state.get("quality_blocked_chapters") or []
    if q_blocked:
        logger.warning(
            "quality_blocked chapters found during standalone final review (not auto-rewritten): %s",
            q_blocked,
        )
