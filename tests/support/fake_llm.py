"""Deterministic offline harness for the generation pipeline.

This module lets the whole LangGraph generation pipeline run end-to-end with
**zero external calls**. It replaces the model-facing agents with deterministic
fakes and installs a benign ``get_llm`` / embedding safety net so any residual
direct LLM call degrades gracefully instead of hitting the network.

Design (see docs plan P0):
- Agent-level fakes give precise control over chapter content and review scores,
  so tests assert orchestration (route order, state transitions, rollback,
  token accounting) rather than model output quality.
- The reviewer score is *scriptable*: a :class:`ScriptedReviewPolicy` decides a
  score per (chapter, distinct-draft) so tests can drive
  ``low -> revise -> rollback_rerun -> converge`` deterministically.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

# ---------------------------------------------------------------------------
# get_llm-level safety net (catches residual direct LLM calls)
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal object compatible with ``response_to_text`` / provider-block probing."""

    def __init__(self, content: str = "{}") -> None:
        self.content = content
        self.response_metadata: dict[str, Any] = {}
        self.additional_kwargs: dict[str, Any] = {}


class FakeLLM:
    """Deterministic stand-in returned by a patched ``get_llm``.

    Returns ``{}`` by default which validates against the reviewer/extractor
    Pydantic schemas (all fields have defaults) and is harmless as summary text.
    """

    def __init__(self, content: str = "{}") -> None:
        self._content = content

    def invoke(self, _prompt: Any, *_args: Any, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(self._content)

    async def ainvoke(self, _prompt: Any, *_args: Any, **_kwargs: Any) -> _FakeResponse:
        return _FakeResponse(self._content)

    def with_structured_output(self, *_args: Any, **_kwargs: Any) -> "FakeLLM":
        return self


def _raise_offline(*_args: Any, **_kwargs: Any):
    """Force embedding to soft-fail locally without touching the network."""
    raise RuntimeError("offline test harness: embeddings disabled")


# ---------------------------------------------------------------------------
# Scriptable review policy
# ---------------------------------------------------------------------------


@dataclass
class ScriptedReviewPolicy:
    """Decide a review score per (chapter, distinct-draft).

    The Nth distinct draft of a scripted chapter scores ``low_score`` while
    ``N <= low_rounds``, then ``high_score``; every other chapter converges on
    its first draft. This drives revise/rollback loops deterministically without
    relying on call-order (safe under the reviewer's thread-pool fan-out).

    Two ways to script which chapters struggle:
    - single-chapter: ``loop_chapter`` + ``low_rounds`` (legacy, default).
    - multi-chapter: ``schedule={chapter_num: low_rounds}`` — authoritative when
      provided, so multiple chapters can each get their own revise/rollback depth
      for long-run stability regressions.
    """

    loop_chapter: int = 1
    low_rounds: int = 3
    low_score: float = 0.30
    high_score: float = 0.92
    schedule: dict[int, int] | None = None

    _seen: dict[int, dict[str, int]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _low_rounds_for(self, chapter_num: int) -> int:
        if self.schedule is not None:
            return int(self.schedule.get(int(chapter_num), 0))
        if int(chapter_num) == int(self.loop_chapter):
            return int(self.low_rounds)
        return 0

    def scripted_chapters(self) -> set[int]:
        """Chapters expected to require at least one revise/rollback."""
        if self.schedule is not None:
            return {int(ch) for ch, rounds in self.schedule.items() if int(rounds) > 0}
        return {int(self.loop_chapter)} if int(self.low_rounds) > 0 else set()

    def _draft_index(self, chapter_num: int, draft_text: str) -> int:
        with self._lock:
            per_chapter = self._seen.setdefault(int(chapter_num), {})
            key = str(draft_text)
            if key not in per_chapter:
                per_chapter[key] = len(per_chapter)
            return per_chapter[key]

    def score_for(self, chapter_num: int, draft_text: str) -> float:
        idx = self._draft_index(chapter_num, draft_text)
        if idx < self._low_rounds_for(chapter_num):
            return float(self.low_score)
        return float(self.high_score)


# ---------------------------------------------------------------------------
# Fake agents
# ---------------------------------------------------------------------------


def _fake_chapter_body(chapter_num: int, attempt: int) -> str:
    """Deterministic multi-paragraph body, distinct per attempt (few paragraphs)."""
    return (
        f"第{chapter_num}章正文（第{attempt}次起草）。"
        "清晨的风穿过长街，少年握紧手中的旧剑，心里第一次生出不肯退让的念头。\n\n"
        "他知道对面的人不会轻易放过自己，但既然已经走到这里，就没有回头的道理。"
        "台阶上的血迹还没干透，远处传来钟声，一声接着一声，像是在替谁数着最后的时辰。\n\n"
        "对峙在一瞬间被打破，剑光与话语同时落下，局势彻底改变，"
        "而这一次，他不再只是被推着走的人，而是主动把主线往前推进了一步。"
    )


class FakeWriterAgent:
    """Deterministic writer: distinct body per (chapter, attempt)."""

    def __init__(self) -> None:
        self._attempts: dict[int, int] = {}

    def run(self, novel_id: Any, chapter_num: int, *_args: Any, **_kwargs: Any) -> str:
        n = self._attempts.get(int(chapter_num), 0) + 1
        self._attempts[int(chapter_num)] = n
        return _fake_chapter_body(int(chapter_num), n)


def _reviewer_pack(score: float, confidence: float, feedback: str) -> dict[str, Any]:
    return {
        "score": float(score),
        "confidence": float(confidence),
        "feedback": feedback,
        "must_fix": [],
        "should_fix": [],
        "positives": ["主线有推进"] if score >= 0.7 else [],
        "highlights": ["情绪张力尚可"] if score >= 0.7 else [],
        "risks": [] if score >= 0.7 else ["推进偏弱"],
        "contradictions": [],
    }


class FakeReviewerAgent:
    """Deterministic reviewer whose score is driven by a ScriptedReviewPolicy."""

    def __init__(self, policy: ScriptedReviewPolicy) -> None:
        self._policy = policy

    def _decide(self, draft: str, chapter_num: int, kind: str) -> dict[str, Any]:
        score = self._policy.score_for(chapter_num, draft)
        confidence = 0.85 if score >= 0.7 else 0.30
        return _reviewer_pack(score, confidence, f"{kind}反馈(score={score:.2f})")

    def run_structured(self, draft, chapter_num=0, *_a, **_k) -> dict[str, Any]:
        return self._decide(draft, chapter_num, "结构")

    def run(self, draft, chapter_num=0, *_a, **_k):
        pack = self._decide(draft, chapter_num, "结构")
        return pack["score"], pack["feedback"]

    def run_factual_structured(self, draft, chapter_num=0, *_a, **_k) -> dict[str, Any]:
        return self._decide(draft, chapter_num, "事实")

    def run_factual(self, draft, chapter_num=0, *_a, **_k):
        pack = self._decide(draft, chapter_num, "事实")
        return pack["score"], pack["feedback"], []

    def run_progression_structured(self, draft, chapter_num=0, *_a, **_k) -> dict[str, Any]:
        pack = self._decide(draft, chapter_num, "推进")
        pack.update(
            {
                "duplicate_beats": [],
                "no_new_delta": [],
                "repeated_reveal": [],
                "repeated_relationship_turn": [],
                "transition_conflict": [],
            }
        )
        return pack

    def run_aesthetic_structured(self, draft, chapter_num=0, *_a, **_k) -> dict[str, Any]:
        return self._decide(draft, chapter_num, "审美")

    def run_aesthetic(self, draft, chapter_num=0, *_a, **_k):
        pack = self._decide(draft, chapter_num, "审美")
        return pack["score"], pack["feedback"], []

    def run_combined(self, draft, chapter_num=0, *_a, **_k):
        struct = self.run_structured(draft, chapter_num)
        factual = self.run_factual_structured(draft, chapter_num)
        factual["contradictions"] = []
        progression = self.run_progression_structured(draft, chapter_num)
        aesthetic = self.run_aesthetic_structured(draft, chapter_num)
        ai_flavor = {"score": 8, "issues": []}
        webnovel = {"score": 8, "violations": []}
        return struct, factual, progression, aesthetic, ai_flavor, webnovel

    def run_cross_chapter_check(self, *_a, **_k) -> dict[str, Any]:
        return {"contradictions": []}

    def run_unknown_character_check(self, *_a, **_k) -> dict[str, Any]:
        return {"verdicts": []}


class FakeFinalizerAgent:
    """Deterministic finalizer: returns the draft unchanged."""

    def run(self, draft: str, _feedback: str = "", *_args: Any, **_kwargs: Any) -> str:
        return str(draft or "")


class FakePrewritePlannerAgent:
    """Return schema-correct prewrite artifacts without any LLM call."""

    def run(self, novel: dict, num_chapters: int, *_args: Any, **_kwargs: Any) -> dict:
        from app.services.generation.agents import _default_prewrite_component

        return {
            key: _default_prewrite_component(key, novel, num_chapters)
            for key in ("constitution", "specification", "creative_plan", "tasks")
        }


class FakeOutlinerAgent:
    """Return normalized, valid outlines for a chapter range without an LLM call."""

    def run_volume_outlines(
        self,
        *,
        start_chapter: int,
        num_chapters: int,
        **_kwargs: Any,
    ) -> list[dict]:
        from app.services.generation.agents import _normalize_outline_item

        outlines: list[dict] = []
        for offset in range(int(num_chapters)):
            ch = int(start_chapter) + offset
            outlines.append(
                _normalize_outline_item(
                    {
                        "title": f"第{ch}章 转折",
                        "outline": f"第{ch}章：主角面对新的冲突并推动主线向前发展，留下悬念钩子。",
                        "purpose": "推进主线并形成阶段兑现",
                        "hook": "章末出现新的威胁",
                        "payoff": "回收上一处伏笔",
                    },
                    ch,
                )
            )
        return outlines

    def run(self, _novel_id: Any, chapter_num: int, *_args: Any, **_kwargs: Any) -> dict:
        from app.services.generation.agents import _normalize_outline_item

        return _normalize_outline_item(
            {
                "title": f"第{chapter_num}章 过渡",
                "outline": f"第{chapter_num}章：承接上文并推进主线，收束部分悬念。",
                "purpose": "承上启下推进主线",
            },
            int(chapter_num),
        )


class FakeFactExtractorAgent:
    """No-op fact extraction (returns empty structured payloads)."""

    def run(self, *_args: Any, **_kwargs: Any) -> dict:
        return {"events": [], "entities": [], "facts": []}

    def run_foreshadow_extraction(self, *_args: Any, **_kwargs: Any) -> dict:
        return {"planted": [], "resolved": []}

    async def run_relation_extraction(self, *_args: Any, **_kwargs: Any):
        from app.services.generation.agents import CharacterRelationsSchema

        return CharacterRelationsSchema()


class FakeProgressionMemoryAgent:
    """No-op progression-memory extraction."""

    def run(self, *_args: Any, **_kwargs: Any) -> dict:
        return {
            "advancement": {},
            "transition": {},
            "advancement_confidence": 0.0,
            "transition_confidence": 0.0,
            "validation_notes": [],
        }


class FakeFinalReviewerAgent:
    """Deterministic final review that always passes."""

    def run(self, content: str, _language: str = "zh") -> bool:
        return bool(content and len(content) >= 100)

    def run_full_book(self, *_args: Any, **_kwargs: Any) -> dict:
        return {
            "score": 0.85,
            "feedback": "ok",
            "confidence": 0.8,
            "must_fix": [],
            "should_improve": [],
            "fallback": False,
        }


# ---------------------------------------------------------------------------
# Seeding + installation
# ---------------------------------------------------------------------------


def seed_novel(
    *,
    title: str = "Offline Harness Novel",
    strategy: str = "web-novel",
    target_language: str = "zh",
    config: dict[str, Any] | None = None,
) -> tuple[int, int]:
    """Create a Novel + default NovelVersion, returning ``(novel_id, version_id)``."""
    from app.core.database import SessionLocal
    from app.models.novel import Novel, NovelVersion

    db = SessionLocal()
    try:
        novel = Novel(
            title=title,
            strategy=strategy,
            target_language=target_language,
            genre="玄幻",
            style="热血",
            audience="男频",
            writing_method="三幕结构",
            user_idea="少年逆袭夺回家族荣光",
            config=config or {},
            status="generating",
        )
        db.add(novel)
        db.commit()
        db.refresh(novel)
        version = NovelVersion(
            novel_id=novel.id, version_no=1, status="generating", is_default=1
        )
        db.add(version)
        db.commit()
        db.refresh(version)
        return int(novel.id), int(version.id)
    finally:
        db.close()


@dataclass
class OfflineHarness:
    """Observability handles returned by :func:`install_offline_harness`."""

    node_trace: list[str]
    rollback_calls: list[dict[str, Any]]
    review_policy: ScriptedReviewPolicy


def install_offline_harness(
    monkeypatch,
    *,
    review_policy: ScriptedReviewPolicy | None = None,
) -> OfflineHarness:
    """Patch agents + LLM/embedding boundary + observability spies.

    Returns an :class:`OfflineHarness` exposing the executed node trace and any
    progression-rollback invocations.
    """
    policy = review_policy or ScriptedReviewPolicy()

    import app.core.llm as llm_mod
    import app.services.generation.graph as graph_mod
    import app.services.generation.nodes.init_node as init_mod
    import app.services.generation.nodes.review as review_mod

    # --- agent-level fakes (constructed inside node_init) ---
    monkeypatch.setattr(init_mod, "WriterAgent", lambda: FakeWriterAgent())
    monkeypatch.setattr(init_mod, "ReviewerAgent", lambda: FakeReviewerAgent(policy))
    monkeypatch.setattr(init_mod, "FinalizerAgent", lambda: FakeFinalizerAgent())
    monkeypatch.setattr(init_mod, "PrewritePlannerAgent", lambda: FakePrewritePlannerAgent())
    monkeypatch.setattr(init_mod, "OutlinerAgent", lambda: FakeOutlinerAgent())
    monkeypatch.setattr(init_mod, "FactExtractorAgent", lambda: FakeFactExtractorAgent())
    monkeypatch.setattr(
        init_mod, "ProgressionMemoryAgent", lambda: FakeProgressionMemoryAgent()
    )
    monkeypatch.setattr(init_mod, "FinalReviewerAgent", lambda: FakeFinalReviewerAgent())

    # --- LLM / embedding safety net (residual direct calls) ---
    monkeypatch.setattr(llm_mod, "get_llm", lambda *a, **k: FakeLLM())
    monkeypatch.setattr(llm_mod, "get_llm_with_fallback", lambda *a, **k: FakeLLM())
    monkeypatch.setattr(llm_mod, "get_embedding_model", _raise_offline)

    # --- language-quality safety net ---
    # ``evaluate_language_quality`` instantiates ``language_tool_python.LanguageTool``
    # (downloads a ~200MB Java package on first use) and, if the local server is
    # unavailable, falls back to the *public* LanguageTool API — a real network call
    # made once per finalized chapter. Both break the "zero external calls" contract
    # and blow up CI wall-time. Stub it to a deterministic passing score.
    import app.core.i18n as i18n_mod
    import app.services.generation.nodes.finalize as finalize_mod

    def _fake_language_quality(text: str, lang_code: str) -> tuple[float, str]:
        return 0.95, "offline-stub"

    monkeypatch.setattr(i18n_mod, "evaluate_language_quality", _fake_language_quality)
    monkeypatch.setattr(
        finalize_mod, "evaluate_language_quality", _fake_language_quality
    )

    # --- observability: node execution trace ---
    node_trace: list[str] = []
    real_log_event: Callable[..., Any] = graph_mod.log_event

    def _tracing_log_event(logger_, event, *args, **kwargs):
        if event == "pipeline.node.start":
            node = kwargs.get("node")
            if node is not None:
                node_trace.append(str(node))
        return real_log_event(logger_, event, *args, **kwargs)

    monkeypatch.setattr(graph_mod, "log_event", _tracing_log_event)

    # --- observability: progression rollback spy ---
    rollback_calls: list[dict[str, Any]] = []
    real_rollback = review_mod.rollback_progression_range

    def _spy_rollback(*args, **kwargs):
        rollback_calls.append(dict(kwargs))
        return real_rollback(*args, **kwargs)

    monkeypatch.setattr(review_mod, "rollback_progression_range", _spy_rollback)

    return OfflineHarness(
        node_trace=node_trace,
        rollback_calls=rollback_calls,
        review_policy=policy,
    )
