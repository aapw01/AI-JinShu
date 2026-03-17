"""Shared constants and helpers for generation pipeline modules."""
import logging
import re
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.core.database import SessionLocal
from app.models.novel import ChapterOutline, NovelSpecification
from app.prompts import render_prompt
from app.services.memory.character_state import CharacterStateManager
from app.services.memory.progression_state import normalize_outline_contract

logger = logging.getLogger("app.services.generation")

REVIEW_SCORE_THRESHOLD = 0.7
MAX_RETRIES = 2
_PLACEHOLDER_TITLE_RE = re.compile(
    r"^\s*(第\s*[零一二三四五六七八九十百千两\d]+\s*章|chapter\s*\d+|ch\s*\d+)\s*$",
    re.IGNORECASE,
)
_PURE_SYMBOL_TITLE_RE = re.compile(r"^[#*_`~\-\s·•|/\\]+$")
_CHAPTER_HEADING_LINE_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*\*)?\s*第\s*[零一二三四五六七八九十百千两\d]+\s*章(?:[：:\s·\-].*)?(?:\*\*)?\s*$",
    re.IGNORECASE,
)
_PREFACE_SEPARATOR_RE = re.compile(r"^\s*[-_*]{3,}\s*$")
_PREFACE_LIST_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)、])\s+")
_PREFACE_BOLD_ITEM_RE = re.compile(r"^\s*\*\*[^*]+\*\*\s*[:：]")
_CHAPTER_BODY_TAG_RE = re.compile(r"<chapter_body>(.*?)</chapter_body>", re.IGNORECASE | re.DOTALL)
_META_TITLE_PHRASES = (
    "以下是根据反馈",
    "重点解决如下问题",
    "原章节内容",
    "人工标注",
    "修订版",
    "全面打磨",
    "提示词",
    "保持原有",
)
_LEADING_META_PREFIXES = (
    "以下是根据反馈",
    "重点解决如下问题",
    "原章节内容",
    "人工标注",
    "要求：",
    "要求:",
    "保留原有",
)
_STRONG_PREFACE_MARKERS = (
    "以下是根据反馈",
    "重点解决如下问题",
    "原章节内容",
    "人工标注",
)
_OUTLINE_METADATA_KEYS = (
    "role",
    "purpose",
    "suspense_level",
    "foreshadowing",
    "plot_twist_level",
    "hook",
    "payoff",
    "mini_climax",
    "summary",
    "chapter_objective",
    "required_new_information",
    "required_irreversible_change",
    "relationship_delta",
    "conflict_axis",
    "payoff_kind",
    "reveal_kind",
    "forbidden_repeats",
    "opening_scene",
    "opening_character_positions",
    "opening_time_state",
    "transition_mode",
)


def _strip_markdown_wrappers(text: str) -> str:
    out = str(text or "").strip()
    out = re.sub(r"^[#*_`~\-\s]+", "", out)
    out = re.sub(r"[#*_`~\-\s]+$", "", out)
    return out.strip("：:|·- ").strip()


def _looks_like_meta_text(text: str) -> bool:
    source = str(text or "").strip()
    if not source:
        return False
    lowered = source.lower()
    if "feedback" in lowered and "chapter" in lowered:
        return True
    for phrase in _META_TITLE_PHRASES:
        if phrase in source:
            return True
    return False


def _is_preface_line(line: str, *, dropped_any: bool = False) -> bool:
    stripped = str(line or "").strip()
    if not stripped:
        return True
    if _PREFACE_SEPARATOR_RE.match(stripped):
        return True
    if _CHAPTER_HEADING_LINE_RE.match(stripped):
        return True
    if dropped_any and _PREFACE_BOLD_ITEM_RE.match(stripped):
        return True
    if dropped_any and _PREFACE_LIST_RE.match(stripped):
        return True
    if _looks_like_meta_text(stripped):
        return True
    for prefix in _LEADING_META_PREFIXES:
        if stripped.startswith(prefix):
            return True
    return False


def normalize_title_text(title: str | None) -> str:
    """Normalize title text by trimming and collapsing repeated chapter prefixes."""
    text = str(title or "").replace("\r", "\n").strip()
    if not text:
        return ""
    if "\n" in text:
        first_non_empty = next((line for line in text.split("\n") if line.strip()), "")
        text = first_non_empty or text
    text = _strip_markdown_wrappers(text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(
        r"^(第\s*[零一二三四五六七八九十百千两\d]+\s*章)\s*\1+",
        r"\1",
        text,
        flags=re.IGNORECASE,
    )
    return text[:80].strip()


def is_effective_title(title: str | None, chapter_num: int | None = None) -> bool:
    """Check if title is meaningful (not empty and not chapter-number placeholder)."""
    text = normalize_title_text(title)
    if not text:
        return False
    if _PURE_SYMBOL_TITLE_RE.match(text):
        return False
    if not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", text):
        return False
    if _PLACEHOLDER_TITLE_RE.match(text):
        return False
    if _looks_like_meta_text(text):
        return False
    if chapter_num is not None:
        chapter_str = str(chapter_num)
        if text in {f"第{chapter_str}章", f"Chapter {chapter_str}", f"CH {chapter_str}", f"Ch {chapter_str}"}:
            return False
        remainder = re.sub(
            r"^第\s*[零一二三四五六七八九十百千两\d]+\s*章[:：\s·\-]*",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        if not remainder:
            return False
        if _looks_like_meta_text(remainder):
            return False
    return True


def normalize_outline_payload(chapter_num: int, outline: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize outline payloads so runtime-added chapters match full-book outline storage."""
    item = normalize_outline_contract(dict(outline or {}), chapter_num)
    normalized_title = normalize_title_text(item.get("title"))
    if not is_effective_title(normalized_title, chapter_num):
        normalized_title = f"第{chapter_num}章"
    return {
        "chapter_num": int(chapter_num),
        "title": normalized_title,
        "outline": item.get("outline", "") or "",
        **{key: item.get(key) for key in _OUTLINE_METADATA_KEYS},
    }


def _outline_metadata_payload(outline: dict[str, Any]) -> dict[str, Any]:
    return {key: outline.get(key) for key in _OUTLINE_METADATA_KEYS}


def _outline_stmt(*, novel_id: int, chapter_num: int, novel_version_id: int | None):
    stmt = select(ChapterOutline).where(
        ChapterOutline.novel_id == int(novel_id),
        ChapterOutline.chapter_num == int(chapter_num),
    )
    if novel_version_id is None:
        stmt = stmt.where(ChapterOutline.novel_version_id.is_(None))
    else:
        stmt = stmt.where(ChapterOutline.novel_version_id == int(novel_version_id))
    return stmt


def upsert_chapter_outline(
    novel_id: int,
    outline: dict[str, Any],
    *,
    novel_version_id: int | None = None,
    db=None,
) -> dict[str, Any]:
    """Create or update a single chapter outline row and return the normalized payload."""
    chapter_num = int(outline.get("chapter_num") or 0)
    if chapter_num <= 0:
        raise ValueError("chapter_num is required for chapter outline upsert")

    normalized = normalize_outline_payload(chapter_num, outline)
    owns_session = db is None
    session = db or SessionLocal()
    try:
        existing = session.execute(
            _outline_stmt(
                novel_id=novel_id,
                chapter_num=normalized["chapter_num"],
                novel_version_id=novel_version_id,
            )
        ).scalar_one_or_none()
        if existing:
            existing.title = normalized["title"]
            existing.outline = normalized["outline"]
            existing.metadata_ = _outline_metadata_payload(normalized)
        else:
            session.add(
                ChapterOutline(
                    novel_id=int(novel_id),
                    novel_version_id=int(novel_version_id) if novel_version_id is not None else None,
                    chapter_num=normalized["chapter_num"],
                    title=normalized["title"],
                    outline=normalized["outline"],
                    metadata_=_outline_metadata_payload(normalized),
                )
            )
        if owns_session:
            session.commit()
        return normalized
    except Exception:
        if owns_session:
            session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def _extract_title_suffix(text: str | None) -> str:
    source = str(text or "").strip()
    if not source:
        return ""
    if _looks_like_meta_text(source):
        return ""
    source = source.replace("\r", "\n")
    source = re.sub(r"第\s*[零一二三四五六七八九十百千两\d]+\s*章[:：]?", "", source)
    parts = re.split(r"[。！？!?\n；;]", source)
    for part in parts:
        segment = part.strip()
        if not segment:
            continue
        segment = _strip_markdown_wrappers(segment)
        segment = re.split(r"[，,、]", segment)[0].strip()
        segment = re.sub(r"^(本章|这一章|该章|章节)\s*", "", segment)
        if len(segment) < 2:
            continue
        if _looks_like_meta_text(segment):
            continue
        if segment in {"推进主线", "推进剧情", "无", "none", "None"}:
            continue
        if len(segment) > 18:
            segment = segment[:18].rstrip("，,、:： ")
        if segment:
            return segment
    return ""


def resolve_chapter_title(
    chapter_num: int,
    title: str | None = None,
    outline: dict | None = None,
    summary: str | None = None,
    content: str | None = None,
) -> str:
    """Resolve a readable chapter title without overriding valid existing titles."""
    normalized = normalize_title_text(title)
    if is_effective_title(normalized, chapter_num):
        return normalized

    outline_obj = outline or {}
    candidates = [
        _extract_title_suffix(outline_obj.get("summary")),
        _extract_title_suffix(outline_obj.get("purpose")),
        _extract_title_suffix(outline_obj.get("outline")),
        _extract_title_suffix(summary),
        _extract_title_suffix(content),
    ]
    suffix = next((x for x in candidates if x), "")
    if suffix:
        return normalize_title_text(f"第{chapter_num}章：{suffix}")
    return f"第{chapter_num}章：关键事件"


def sanitize_chapter_content_for_storage(raw: str | None, chapter_num: int) -> str:
    """Best-effort sanitizer (deprecated in write path; keep for manual/offline cleanup only)."""
    source = str(raw or "").replace("\r\n", "\n").replace("\r", "\n")
    if not source.strip():
        return ""

    lines = source.split("\n")
    # Only sanitize when contamination signal is strong enough to avoid deleting valid openings.
    strong_signal = 0
    scanned = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        scanned += 1
        if any(stripped.startswith(marker) for marker in _STRONG_PREFACE_MARKERS):
            strong_signal += 1
        elif _CHAPTER_HEADING_LINE_RE.match(stripped):
            strong_signal += 1
        elif _PREFACE_SEPARATOR_RE.match(stripped):
            strong_signal += 1
        if scanned >= 8:
            break
    if strong_signal == 0:
        return source.strip()

    idx = 0
    dropped_any = False

    while idx < len(lines):
        stripped = lines[idx].strip()
        if not stripped:
            idx += 1
            continue
        if _is_preface_line(stripped, dropped_any=dropped_any):
            dropped_any = True
            idx += 1
            continue
        break

    cleaned_lines = lines[idx:] if dropped_any else lines
    if cleaned_lines and _CHAPTER_HEADING_LINE_RE.match(cleaned_lines[0].strip() or ""):
        cleaned_lines = cleaned_lines[1:]
        while cleaned_lines and not cleaned_lines[0].strip():
            cleaned_lines = cleaned_lines[1:]

    cleaned = "\n".join(cleaned_lines).strip()
    if not cleaned:
        logger.warning("chapter.content.sanitize.empty_fallback chapter=%s", chapter_num)
        return source.strip()
    return cleaned


def detect_chapter_content_contamination(raw: str | None, chapter_num: int | None = None) -> dict:
    """Detect prompt/meta leakage without mutating content."""
    text = str(raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return {"contaminated": False, "reasons": [], "evidence": []}

    reasons: list[str] = []
    evidence: list[str] = []
    non_empty: list[str] = [line.strip() for line in text.split("\n") if line.strip()]
    head = non_empty[:10]

    for idx, line in enumerate(head):
        if any(line.startswith(marker) for marker in _STRONG_PREFACE_MARKERS):
            reasons.append("strong_preface_marker")
            evidence.append(line[:80])
        if idx <= 2 and _looks_like_meta_text(line):
            reasons.append("meta_phrase")
            evidence.append(line[:80])
        if idx == 0 and _CHAPTER_HEADING_LINE_RE.match(line):
            reasons.append("leading_chapter_heading")
            evidence.append(line[:80])
        if idx <= 3 and _PREFACE_SEPARATOR_RE.match(line):
            reasons.append("preface_separator")
            evidence.append(line[:80])
        if idx <= 4 and _PREFACE_BOLD_ITEM_RE.match(line):
            reasons.append("preface_bold_item")
            evidence.append(line[:80])
        if idx <= 4 and _PREFACE_LIST_RE.match(line):
            if any(r in reasons for r in ("strong_preface_marker", "meta_phrase", "preface_separator", "preface_bold_item")):
                reasons.append("preface_list_item")
                evidence.append(line[:80])

    if chapter_num is not None and head:
        first = head[0]
        chapter_plain = normalize_title_text(first)
        if chapter_plain in {
            f"第{chapter_num}章",
            f"Chapter {chapter_num}",
            f"CH {chapter_num}",
            f"Ch {chapter_num}",
        }:
            reasons.append("leading_placeholder_heading")
            evidence.append(first[:80])

    uniq_reasons = list(dict.fromkeys(reasons))
    uniq_evidence = list(dict.fromkeys(evidence))
    return {"contaminated": bool(uniq_reasons), "reasons": uniq_reasons, "evidence": uniq_evidence[:5]}


def extract_chapter_body_from_response(raw: str | None) -> str:
    """Extract chapter body from tag-constrained model outputs."""
    text = str(raw or "").strip()
    if not text:
        return ""
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    match = _CHAPTER_BODY_TAG_RE.search(text)
    if not match:
        return text
    body = (match.group(1) or "").strip()
    return body or text

_HTML_BLOCK_TAG_RE = re.compile(r"</?(?:p|div|br)\s*/?>", re.IGNORECASE)
_ESCAPED_NEWLINE_RE = re.compile(r"(?<!\\)\\n")
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u00a0]")
_CONSECUTIVE_BLANK_LINES_RE = re.compile(r"\n{3,}")


def normalize_chapter_content(raw: str | None) -> str:
    """Normalize chapter content to a canonical format with \\n\\n paragraph breaks.

    Handles CRLF, escaped newlines, HTML tags, code fences, <chapter_body> wrappers,
    zero-width characters, and compresses excessive blank lines.
    """
    text = str(raw or "")
    if not text.strip():
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _ZERO_WIDTH_RE.sub("", text)
    text = text.lstrip("\ufeff")

    if "<chapter_body>" in text.lower():
        match = _CHAPTER_BODY_TAG_RE.search(text)
        if match:
            text = (match.group(1) or "").strip()

    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)

    text = "\n".join(
        ln for ln in text.split("\n") if not re.match(r"^\s*```\w*\s*$", ln)
    )

    text = _HTML_BLOCK_TAG_RE.sub("\n", text)
    text = _ESCAPED_NEWLINE_RE.sub("\n", text)

    lines = text.split("\n")
    cleaned: list[str] = []
    for line in lines:
        cleaned.append(line.rstrip())
    text = "\n".join(cleaned)

    paragraphs = re.split(r"\n\s*\n", text)
    result_parts: list[str] = []
    for para in paragraphs:
        stripped = para.strip()
        if not stripped:
            continue
        inner_lines = [ln.strip() for ln in stripped.split("\n") if ln.strip()]
        result_parts.append("\n".join(inner_lines))

    result = "\n\n".join(result_parts).strip()
    result = _CONSECUTIVE_BLANK_LINES_RE.sub("\n\n", result)
    return result


def load_prewrite_artifacts(novel_id: int) -> dict:
    """Load existing prewrite specification artifacts from the database."""
    db = SessionLocal()
    try:
        rows = db.execute(
            select(NovelSpecification).where(NovelSpecification.novel_id == novel_id)
        ).scalars().all()
        if not rows:
            return {}
        return {r.spec_type: r.content for r in rows}
    finally:
        db.close()


def load_outlines_from_db(
    novel_id: int, novel_version_id: int | None = None
) -> list[dict]:
    """Load existing chapter outlines from the database, sorted by chapter_num."""
    db = SessionLocal()
    try:
        stmt = (
            select(ChapterOutline)
            .where(ChapterOutline.novel_id == novel_id)
        )
        if novel_version_id is not None:
            stmt = stmt.where(ChapterOutline.novel_version_id == novel_version_id)
        stmt = stmt.order_by(ChapterOutline.chapter_num)
        rows = db.execute(stmt).scalars().all()
        return [
            {
                "chapter_num": r.chapter_num,
                "title": r.title,
                "outline": r.outline,
                **(r.metadata_ if isinstance(r.metadata_, dict) else {}),
            }
            for r in rows
        ]
    finally:
        db.close()


def save_prewrite_artifacts(novel_id: int, prewrite: dict) -> None:
    db = SessionLocal()
    try:
        for spec_type, content in prewrite.items():
            stmt = select(NovelSpecification).where(
                NovelSpecification.novel_id == novel_id,
                NovelSpecification.spec_type == spec_type,
            )
            existing = db.execute(stmt).scalar_one_or_none()
            if existing:
                existing.content = content
            else:
                try:
                    with db.begin_nested():
                        db.add(NovelSpecification(novel_id=novel_id, spec_type=spec_type, content=content))
                        db.flush()
                except IntegrityError:
                    existing = db.execute(stmt).scalar_one_or_none()
                    if existing:
                        existing.content = content
                    else:
                        raise
        db.commit()
    finally:
        db.close()


def save_full_outlines(novel_id: int, outlines: list[dict], novel_version_id: int | None = None, *, replace_all: bool = True) -> None:
    db = SessionLocal()
    try:
        if replace_all:
            del_stmt = delete(ChapterOutline).where(ChapterOutline.novel_id == novel_id)
            if novel_version_id is not None:
                del_stmt = del_stmt.where(ChapterOutline.novel_version_id == novel_version_id)
            db.execute(del_stmt)
        else:
            chapter_nums = [o.get("chapter_num") for o in outlines if o.get("chapter_num") is not None]
            if chapter_nums:
                del_stmt = delete(ChapterOutline).where(
                    ChapterOutline.novel_id == novel_id,
                    ChapterOutline.chapter_num.in_(chapter_nums),
                )
                if novel_version_id is not None:
                    del_stmt = del_stmt.where(ChapterOutline.novel_version_id == novel_version_id)
                db.execute(del_stmt)
        for o in outlines:
            upsert_chapter_outline(
                novel_id=novel_id,
                outline=o,
                novel_version_id=novel_version_id,
                db=db,
            )
        db.commit()
    finally:
        db.close()


def generate_chapter_summary(
    content: str, outline: dict, chapter_num: int, language: str, strategy: str
) -> str:
    from app.core.llm import get_llm_with_fallback, response_to_text
    from app.core.strategy import get_model_for_stage

    provider, model = get_model_for_stage(strategy, "reviewer")
    llm = get_llm_with_fallback(provider, model)
    prompt = render_prompt(
        "chapter_summary_generate",
        chapter_num=chapter_num,
        content=(content[:4000]),
        language=language,
    )
    try:
        resp = llm.invoke(prompt)
        return response_to_text(resp).strip()[:800]
    except Exception:
        return outline.get("summary") or f"第{chapter_num}章摘要"


def update_character_states_from_content(
    novel_id: int,
    chapter_num: int,
    content: str,
    prewrite: dict,
    char_mgr: CharacterStateManager,
    language: str,
    strategy: str,
    db=None,
    novel_version_id: int | None = None,
) -> None:
    from app.core.llm import get_llm_with_fallback, response_to_text
    from app.core.strategy import get_model_for_stage
    from app.services.generation.agents import _parse_json_response

    provider, model = get_model_for_stage(strategy, "reviewer")
    llm = get_llm_with_fallback(provider, model)
    characters = prewrite.get("specification", {}).get("characters", [])
    if not isinstance(characters, list) or not characters:
        return
    char_names = [c.get("name", "") for c in characters if isinstance(c, dict) and c.get("name")]
    if not char_names:
        return
    prompt = render_prompt(
        "character_state_updates_from_chapter",
        character_names=", ".join(char_names),
        chapter_num=chapter_num,
        content=(content[:3000]),
    )
    try:
        resp = llm.invoke(prompt)
        data = _parse_json_response(response_to_text(resp))
        for update in data.get("updates", []):
            if isinstance(update, dict) and update.get("name"):
                payload = {"chapter_num": chapter_num, **update}
                if novel_version_id is None:
                    char_mgr.update_state(novel_id, update["name"], payload, db=db)
                else:
                    try:
                        char_mgr.update_state(
                            novel_id,
                            update["name"],
                            payload,
                            db=db,
                            novel_version_id=novel_version_id,
                        )
                    except TypeError:
                        # Backward-compatible path for legacy managers in tests.
                        char_mgr.update_state(novel_id, update["name"], payload, db=db)
    except Exception as e:
        logger.warning(f"Character state update from content failed: {e}")
