"""Pre-write consistency checker - validates context before chapter generation.

Performs rule-based checks on the context package to catch contradictions
before they enter the LLM prompt. Returns warnings and blockers.
"""
import logging
import re
from dataclasses import dataclass, field

from app.core.database import SessionLocal
from app.models.novel import Chapter

logger = logging.getLogger(__name__)

# Common Chinese words to exclude when detecting potential character names
_NAME_EXCLUDE = frozenset(
    {"可以", "已经", "不是", "这个", "那个", "什么", "怎么", "等等", "因为", "所以", "如果", "但是"}
)


@dataclass
class ConsistencyIssue:
    level: str  # "warning" or "blocker"
    category: str  # "character", "item", "timeline", "plot", "naming"
    message: str
    chapter_ref: int | None = None


@dataclass
class ConsistencyReport:
    issues: list[ConsistencyIssue] = field(default_factory=list)
    passed: bool = True

    @property
    def blockers(self) -> list[ConsistencyIssue]:
        return [i for i in self.issues if i.level == "blocker"]

    @property
    def warnings(self) -> list[ConsistencyIssue]:
        return [i for i in self.issues if i.level == "warning"]

    def summary(self) -> str:
        if not self.issues:
            return "一致性检查通过，无异常。"
        parts = []
        for issue in self.issues:
            prefix = "🚫" if issue.level == "blocker" else "⚠️"
            parts.append(f"{prefix} [{issue.category}] {issue.message}")
        return "\n".join(parts)


def _get_spec_characters(prewrite: dict) -> list[str]:
    """Extract character names from prewrite specification."""
    spec = prewrite.get("specification") or prewrite.get("spec") or {}
    chars = spec.get("characters") or []
    if not isinstance(chars, list):
        return []
    names = []
    for c in chars:
        if isinstance(c, dict) and c.get("name"):
            names.append(str(c["name"]).strip())
        elif isinstance(c, str):
            names.append(c.strip())
    return [n for n in names if n]


def _get_dead_characters(context: dict) -> set[str]:
    """Extract character names marked as dead/deceased from character states."""
    dead = set()
    states = context.get("character_states") or []
    for s in states:
        if not isinstance(s, dict):
            continue
        content = s.get("content") or {}
        if not isinstance(content, dict):
            content = {"raw": str(content)}
        key = s.get("key")
        status = (content.get("status") or content.get("state") or "").lower()
        raw_str = str(content).lower()
        if "dead" in status or "deceased" in status or "死" in raw_str or "亡" in raw_str:
            if key:
                dead.add(str(key))
            name = content.get("name")
            if name:
                dead.add(str(name))
    return dead


def _extract_names_from_text(text: str) -> set[str]:
    """Extract potential character names (2-4 char Chinese) from text."""
    if not text or not isinstance(text, str):
        return set()
    # Match 2-4 consecutive Chinese characters
    matches = re.findall(r"[\u4e00-\u9fff]{2,4}", text)
    return {m for m in matches if m not in _NAME_EXCLUDE}


def _check_character_existence(
    report: ConsistencyReport,
    outline: dict,
    prewrite: dict,
    context: dict,
    chapter_num: int,
) -> None:
    """Character existence: roster check and dead-character blocker."""
    roster = set(_get_spec_characters(prewrite))
    dead = _get_dead_characters(context)

    outline_text = (outline.get("outline") or "") + (outline.get("title") or "") + (outline.get("summary") or "")
    mentioned = _extract_names_from_text(outline_text)

    # Characters not in roster
    not_in_roster = mentioned - roster
    if not_in_roster and roster:
        for name in list(not_in_roster)[:5]:  # Limit to 5 to avoid noise
            report.issues.append(
                ConsistencyIssue("warning", "character", f"大纲提及未在角色表中的角色: {name}", chapter_num)
            )

    # Dead characters used in outline
    used_dead = mentioned & dead
    if used_dead:
        for name in used_dead:
            report.issues.append(
                ConsistencyIssue(
                    "blocker",
                    "character",
                    f"角色「{name}」已标记为死亡/离世，但大纲计划使用该角色",
                    chapter_num,
                )
            )


def _check_foreshadowing_continuity(
    report: ConsistencyReport,
    outline: dict,
    context: dict,
    prewrite: dict,
    novel_id: int,
    chapter_num: int,
) -> None:
    """Foreshadowing: payoff must reference planted foreshadowing."""
    payoff = (outline.get("payoff") or "").strip()
    if not payoff:
        return

    from app.services.memory.thread_ledger import get_thread_ledger

    ledger = get_thread_ledger(novel_id, chapter_num, prewrite)
    active = ledger.get("active_foreshadowing") or []

    # No foreshadowing was ever planted
    if not active:
        report.issues.append(
            ConsistencyIssue(
                "blocker",
                "plot",
                f"第{chapter_num}章试图回收伏笔「{payoff[:50]}...」但此前未埋设任何伏笔",
                chapter_num,
            )
        )
        return

    # Check spec foreshadowing IDs (e.g. F-001)
    spec = prewrite.get("specification") or prewrite.get("spec") or {}
    foreshadow_list = spec.get("foreshadowing") or []
    if isinstance(foreshadow_list, list):
        for fid in re.findall(r"[A-Z]-\d+", payoff):
            for f in foreshadow_list:
                if isinstance(f, dict) and f.get("id") == fid:
                    plant_ch = f.get("plant_chapter")
                    if plant_ch is not None and plant_ch >= chapter_num:
                        report.issues.append(
                            ConsistencyIssue(
                                "blocker",
                                "plot",
                                f"第{chapter_num}章试图回收伏笔「{fid}」，但该伏笔计划在第{plant_ch}章才埋设",
                                chapter_num,
                            )
                        )
                        return

    # Check if payoff references any planted content (substring match)
    planted_texts = [str(a.get("foreshadowing", "")) for a in active if a.get("foreshadowing")]
    if planted_texts:
        payoff_lower = payoff.lower()
        any_match = any(pt and (pt in payoff or payoff_lower in pt.lower()) for pt in planted_texts)
        if not any_match and len(payoff) > 10:
            report.issues.append(
                ConsistencyIssue(
                    "blocker",
                    "plot",
                    f"第{chapter_num}章试图回收伏笔「{payoff[:50]}...」，但该伏笔此前未埋设",
                    chapter_num,
                )
            )


def _check_outline_dependency(
    report: ConsistencyReport,
    context: dict,
    chapter_num: int,
) -> None:
    """Outline dependency: previous chapter summary should exist in context."""
    if chapter_num <= 1:
        return

    summaries = context.get("summaries") or []
    prev_chapters = {s.get("chapter_num") for s in summaries if isinstance(s, dict) and s.get("chapter_num") is not None}
    if (chapter_num - 1) not in prev_chapters:
        report.issues.append(
            ConsistencyIssue(
                "warning",
                "timeline",
                "上一章摘要缺失于上下文中，可能影响连续性",
                chapter_num,
            )
        )


def _check_duplicate_content(
    report: ConsistencyReport,
    novel_id: int,
    chapter_num: int,
) -> None:
    """Duplicate: chapter already exists with completed status."""
    db = SessionLocal()
    try:
        existing = (
            db.query(Chapter)
            .filter(Chapter.novel_id == novel_id, Chapter.chapter_num == chapter_num, Chapter.status == "completed")
            .first()
        )
        if existing:
            report.issues.append(
                ConsistencyIssue(
                    "warning",
                    "plot",
                    f"第{chapter_num}章已存在且状态为completed，将被覆盖",
                    chapter_num,
                )
            )
    finally:
        db.close()


def _check_thread_overload(
    report: ConsistencyReport,
    outline: dict,
    prewrite: dict,
    novel_id: int,
    chapter_num: int,
) -> None:
    """Thread overload: too many unresolved threads."""
    from app.services.memory.thread_ledger import get_thread_ledger

    ledger = get_thread_ledger(novel_id, chapter_num, prewrite)
    active_foreshadowing = ledger.get("active_foreshadowing") or []
    unresolved_hooks = ledger.get("unresolved_hooks") or []

    # Payoff in current outline resolves some; count only active
    count = len(active_foreshadowing) + len(unresolved_hooks)
    if count > 15:
        report.issues.append(
            ConsistencyIssue(
                "warning",
                "plot",
                f"未解伏笔/钩子过多({count}个)，建议先回收部分",
                chapter_num,
            )
        )


def check_consistency(
    novel_id: int,
    chapter_num: int,
    outline: dict,
    context: dict,
    prewrite: dict,
) -> ConsistencyReport:
    """Run all consistency checks before writing a chapter.

    Args:
        novel_id: Novel ID
        chapter_num: Chapter being written
        outline: Current chapter outline dict
        context: The layered context package
        prewrite: Prewrite artifacts (specification, constitution, etc.)

    Returns:
        ConsistencyReport with any issues found
    """
    report = ConsistencyReport()

    _check_character_existence(report, outline, prewrite, context, chapter_num)
    _check_foreshadowing_continuity(report, outline, context, prewrite, novel_id, chapter_num)
    _check_outline_dependency(report, context, chapter_num)
    _check_duplicate_content(report, novel_id, chapter_num)
    _check_thread_overload(report, outline, prewrite, novel_id, chapter_num)

    if report.blockers:
        report.passed = False

    return report


def inject_consistency_context(context: dict, report: ConsistencyReport) -> dict:
    """Add consistency warnings to context so the writer agent can see them."""
    if report.warnings:
        context["consistency_warnings"] = report.summary()
    return context
