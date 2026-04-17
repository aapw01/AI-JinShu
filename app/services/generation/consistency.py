"""Pre-write consistency checker - validates context before chapter generation.

Performs rule-based checks on the context package to catch contradictions
before they enter the LLM prompt. Returns warnings and blockers.
"""
import logging
import re
from dataclasses import dataclass, field
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.novel import ChapterVersion
from app.services.memory.progression_state import normalize_outline_contract, similarity_against_constraints

logger = logging.getLogger(__name__)

# Common Chinese words to exclude when detecting potential character names
_NAME_EXCLUDE = frozenset(
    {"可以", "已经", "不是", "这个", "那个", "什么", "怎么", "等等", "因为", "所以", "如果", "但是"}
)

_NAME_CONTEXT_PATTERNS = [
    r"([\u4e00-\u9fff]{2,4})[说道问答喝叫嗤冷哼]",
    r"([\u4e00-\u9fff]{2,4})[看望转瞥盯扫]向",
    r"叫(?:做|作)?([\u4e00-\u9fff]{2,4})",
    r"名(?:叫|为)([\u4e00-\u9fff]{2,4})",
]


def _extract_names_by_context(text: str) -> set[str]:
    """Extract character names by grammatical context patterns (higher precision)."""
    names = set()
    for pattern in _NAME_CONTEXT_PATTERNS:
        for m in re.finditer(pattern, text):
            name = m.group(1)
            if name and name not in _NAME_EXCLUDE:
                names.add(name)
    return names


def _extract_character_names_freq(text: str, min_count: int = 3) -> set[str]:
    """Extract names appearing >= min_count times (filters out common words)."""
    if not text or not isinstance(text, str):
        return set()
    matches = re.findall(r"[\u4e00-\u9fff]{2,4}", text)
    counter: dict[str, int] = {}
    for m in matches:
        if m not in _NAME_EXCLUDE:
            counter[m] = counter.get(m, 0) + 1
    return {name for name, cnt in counter.items() if cnt >= min_count}


def _build_full_roster(prewrite: dict) -> set[str]:
    """Extract all known name variants (name, alias, title, nickname) from prewrite spec."""
    roster: set[str] = set()
    spec = prewrite.get("specification") or prewrite.get("spec") or {}
    for c in (spec.get("characters") or []):
        if not isinstance(c, dict):
            continue
        for field in ("name", "alias", "title", "nickname"):
            val = c.get(field)
            if isinstance(val, str) and val.strip():
                roster.add(val.strip())
            elif isinstance(val, list):
                roster.update(v.strip() for v in val if isinstance(v, str) and v.strip())
    return roster


def extract_unknown_characters(draft: str, prewrite: dict) -> set[str]:
    """Two-phase extraction: context patterns + frequency filter, minus known roster."""
    context_names = _extract_names_by_context(draft)
    freq_names = _extract_character_names_freq(draft, min_count=3)
    candidates = context_names | freq_names
    full_roster = _build_full_roster(prewrite)
    return candidates - full_roster - _NAME_EXCLUDE


@dataclass
class ConsistencyIssue:
    """ConsistencyIssue。"""
    level: str  # "warning" or "blocker"
    category: str  # "character", "item", "timeline", "plot", "naming"
    message: str
    chapter_ref: int | None = None


@dataclass
class ConsistencyReport:
    """ConsistencyReport。"""
    issues: list[ConsistencyIssue] = field(default_factory=list)
    passed: bool = True

    @property
    def blockers(self) -> list[ConsistencyIssue]:
        """执行 blockers 相关辅助逻辑。"""
        return [i for i in self.issues if i.level == "blocker"]

    @property
    def warnings(self) -> list[ConsistencyIssue]:
        """执行 warnings 相关辅助逻辑。"""
        return [i for i in self.issues if i.level == "warning"]

    def summary(self) -> str:
        """执行 summary 相关辅助逻辑。"""
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


def _check_hard_constraints(
    report: ConsistencyReport,
    outline: dict,
    context: dict,
    chapter_num: int,
) -> None:
    """Hard constraints from story bible must never be violated."""
    constraints = context.get("hard_constraints") or {}
    forbidden = set(str(x) for x in (constraints.get("forbidden_characters") or []) if x)
    if not forbidden:
        return
    outline_text = (outline.get("outline") or "") + (outline.get("title") or "") + (outline.get("summary") or "")
    mentioned = _extract_names_from_text(outline_text)
    violated = mentioned & forbidden
    for name in sorted(violated):
        report.issues.append(
            ConsistencyIssue(
                "blocker",
                "character",
                f"硬约束冲突：角色「{name}」已在 Story Bible 标记为不可出场",
                chapter_num,
            )
        )


def _check_entity_hard_constraints(
    report: ConsistencyReport,
    outline: dict,
    context: dict,
    chapter_num: int,
) -> None:
    """Generic entity hard-constraint validation from constraint registry."""
    constraints = context.get("hard_constraints") or {}
    registry = constraints.get("entity_hard_constraints") or []
    if not isinstance(registry, list) or not registry:
        return
    outline_text = (outline.get("outline") or "") + (outline.get("title") or "") + (outline.get("summary") or "")
    for item in registry[:60]:
        if not isinstance(item, dict):
            continue
        entity = str(item.get("entity") or "").strip()
        if not entity:
            continue
        if entity not in outline_text:
            continue
        ctype = str(item.get("constraint_type") or "").strip()
        if ctype == "forbidden_presence":
            report.issues.append(
                ConsistencyIssue(
                    "blocker",
                    "character",
                    f"硬约束冲突：角色「{entity}」不应在本章正常出场",
                    chapter_num,
                )
            )
            continue
        forbidden_patterns = item.get("forbidden_patterns") or []
        if not isinstance(forbidden_patterns, list):
            continue
        for pattern in forbidden_patterns[:12]:
            token = str(pattern).strip()
            if token and token in outline_text:
                report.issues.append(
                    ConsistencyIssue(
                        "blocker",
                        "character",
                        f"硬约束冲突：角色「{entity}」触发限制动作「{token}」",
                        chapter_num,
                    )
                )
                break


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

    # No foreshadowing was ever planted — warning only; on a fresh first-pass run
    # no prior chapter has completed yet, so active_foreshadowing is always empty.
    # Blocking here would prevent all early chapters from being written.
    if not active:
        report.issues.append(
            ConsistencyIssue(
                "warning",
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
            # Downgraded to warning: substring matching between outline texts is
            # too unreliable for a hard block (paraphrasing or slight wording
            # differences cause false positives).
            report.issues.append(
                ConsistencyIssue(
                    "warning",
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
    novel_version_id: int,
    chapter_num: int,
) -> None:
    """Duplicate: chapter already exists with completed status."""
    db = SessionLocal()
    try:
        stmt = select(ChapterVersion).where(
            ChapterVersion.novel_version_id == novel_version_id,
            ChapterVersion.chapter_num == chapter_num,
            ChapterVersion.status == "completed",
        )
        existing = db.execute(stmt).scalar_one_or_none()
        if existing:
            report.issues.append(
                ConsistencyIssue(
                    "warning",
                    "plot",
                    f"当前版本第{chapter_num}章已存在且状态为completed，将被覆盖",
                    chapter_num,
                )
            )
    finally:
        db.close()


def _check_timeline_conflicts(
    report: ConsistencyReport,
    outline: dict,
    context: dict,
    chapter_num: int,
) -> None:
    """Detect obvious timeline conflicts between outline and story bible context."""
    story_bible_context = str(context.get("story_bible_context") or "")
    if not story_bible_context:
        return
    outline_text = (outline.get("outline") or "") + (outline.get("summary") or "") + (outline.get("title") or "")
    # If previous context explicitly indicates event happened in latest chapter, avoid '数月后' hard jump immediately.
    if chapter_num > 1 and ("ch" + str(chapter_num - 1) + ":") in story_bible_context:
        if "数月后" in outline_text or "一年后" in outline_text:
            report.issues.append(
                ConsistencyIssue(
                    "warning",
                    "timeline",
                    "时间线可能跳跃过大：上一章刚结束，本章出现长时间跃迁，请确认过渡是否充分",
                    chapter_num,
                )
            )


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


def _check_progression_conflicts(
    report: ConsistencyReport,
    outline: dict,
    context: dict,
    chapter_num: int,
) -> None:
    """检查progressionconflicts。"""
    outline_contract = normalize_outline_contract(outline, chapter_num)
    anti_repeat = context.get("anti_repeat_constraints") or {}
    objective = str(outline_contract.get("chapter_objective") or "").strip()
    recent_objectives = [str(x) for x in (anti_repeat.get("recent_objectives") or []) if str(x).strip()]
    matched, similar_objective = similarity_against_constraints(objective, recent_objectives, threshold=0.84)
    if objective and matched:
        report.issues.append(
            ConsistencyIssue(
                "warning",
                "plot",
                f"本章目标与近几章已完成推进高度重叠：{similar_objective[:80]}",
                chapter_num,
            )
        )

    required_information = [str(x) for x in (outline_contract.get("required_new_information") or []) if str(x).strip()]
    already_revealed = [str(x) for x in (anti_repeat.get("book_revealed_information") or []) if str(x).strip()]
    for item in required_information[:4]:
        revealed, matched_info = similarity_against_constraints(item, already_revealed, threshold=0.82)
        if revealed:
            report.issues.append(
                ConsistencyIssue(
                    "blocker",
                    "plot",
                    f"本章计划揭示的信息疑似已在前文揭示：{matched_info[:80]}",
                    chapter_num,
                )
            )

    relationship_delta = str(outline_contract.get("relationship_delta") or "").strip()
    relationship_history = [str(x) for x in (anti_repeat.get("recent_relationship_deltas") or []) if str(x).strip()]
    repeated_relationship, matched_relationship = similarity_against_constraints(
        relationship_delta,
        relationship_history,
        threshold=0.82,
    )
    if relationship_delta and repeated_relationship:
        report.issues.append(
            ConsistencyIssue(
                "warning",
                "plot",
                f"本章关系推进疑似重复最近章节：{matched_relationship[:80]}",
                chapter_num,
            )
        )


def _check_transition_conflicts(
    report: ConsistencyReport,
    outline: dict,
    context: dict,
    chapter_num: int,
) -> None:
    """检查transitionconflicts。"""
    if chapter_num <= 1:
        return
    outline_contract = normalize_outline_contract(outline, chapter_num)
    previous_transition = context.get("previous_transition_state") or {}
    opening_scene = str(outline_contract.get("opening_scene") or "").strip()
    transition_mode = str(outline_contract.get("transition_mode") or "").strip().lower()
    previous_scene = str(previous_transition.get("ending_scene") or "").strip()
    previous_action = str(previous_transition.get("last_action") or "").strip()
    scene_exit = str(previous_transition.get("scene_exit") or previous_transition.get("unresolved_exit") or "").strip()

    if previous_scene and opening_scene and transition_mode in {"direct", "continuous", ""}:
        scene_conflict, matched_scene = similarity_against_constraints(opening_scene, [previous_scene], threshold=0.32)
        if not scene_conflict and scene_exit:
            report.issues.append(
                ConsistencyIssue(
                    "blocker",
                    "timeline",
                    f"上一章结束于「{previous_scene}」，且存在场景出口「{scene_exit}」，本章开场「{opening_scene}」缺少过渡",
                    chapter_num,
                )
            )
    if previous_action and any(token in previous_action for token in ["离开", "摔门", "冲出", "被带走", "昏迷", "倒下"]) and transition_mode in {"direct", "continuous", ""}:
        if opening_scene:
            report.issues.append(
                ConsistencyIssue(
                    "warning",
                    "timeline",
                    f"上一章以「{previous_action[:40]}」结束，本章开场需显式交代过渡到「{opening_scene}」",
                    chapter_num,
                )
            )


def check_consistency(
    novel_id: int,
    novel_version_id: int,
    chapter_num: int,
    outline: dict,
    context: dict,
    prewrite: dict,
) -> ConsistencyReport:
    """Run all consistency checks before writing a chapter.

    Args:
        novel_id: Novel ID
        novel_version_id: Active novel version ID
        chapter_num: Chapter being written
        outline: Current chapter outline dict
        context: The layered context package
        prewrite: Prewrite artifacts (specification, constitution, etc.)

    Returns:
        ConsistencyReport with any issues found
    """
    report = ConsistencyReport()

    _check_character_existence(report, outline, prewrite, context, chapter_num)
    _check_hard_constraints(report, outline, context, chapter_num)
    _check_entity_hard_constraints(report, outline, context, chapter_num)
    _check_foreshadowing_continuity(report, outline, context, prewrite, novel_id, chapter_num)
    _check_outline_dependency(report, context, chapter_num)
    _check_duplicate_content(report, novel_version_id, chapter_num)
    _check_timeline_conflicts(report, outline, context, chapter_num)
    _check_thread_overload(report, outline, prewrite, novel_id, chapter_num)
    _check_progression_conflicts(report, outline, context, chapter_num)
    _check_transition_conflicts(report, outline, context, chapter_num)

    if report.blockers:
        report.passed = False

    return report


def inject_consistency_context(context: dict, report: ConsistencyReport) -> dict:
    """Add consistency warnings and blockers to context so the writer agent can see them."""
    if report.warnings:
        context["consistency_warnings"] = "\n".join(
            f"⚠️ [{i.category}] {i.message}" for i in report.warnings
        )
    if report.blockers:
        context["consistency_blockers"] = "\n".join(
            f"🚫 [{i.category}] {i.message}" for i in report.blockers
        )
    return context
