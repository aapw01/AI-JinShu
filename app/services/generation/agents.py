"""LangGraph pipeline agents - modular classes with language and model selection."""
import json
import logging
import re
import time
import hashlib
from typing import Any
from pydantic import BaseModel, Field, ValidationError
from langchain_core.output_parsers import PydanticOutputParser

from app.core.llm import extract_provider_block, get_llm_with_fallback, response_to_text, _is_retryable
from app.core.strategy import resolve_ai_profile
from app.core.llm_contract import invoke_chapter_body_structured
from app.prompts import render_prompt
from app.services.generation.common import (
    is_outline_content_valid,
    load_outlines_from_db,
    normalize_title_text,
    save_full_outlines,
)
from app.services.generation.contracts import OutputContractError
from app.services.generation.length_control import build_chapter_length_prompt_kwargs
from app.services.generation.prompt_sections import (
    build_fact_extractor_role_section,
    build_finalizer_role_section,
    build_memory_governance_sections,
    build_progression_role_section,
    build_reviewer_role_section,
    build_style_overlay_sections,
    build_writer_role_section,
)
from app.services.memory.progression_state import normalize_outline_contract

logger = logging.getLogger(__name__)

_AGENT_MAX_RETRIES = 3
_AGENT_RETRY_BACKOFF = [2.0, 5.0, 10.0]
_OUTLINE_BATCH_SIZE = 10
_HARMONIZABLE_OUTLINE_FIELDS = {
    "payoff",
    "reveal_kind",
    "forbidden_repeats",
    "opening_scene",
    "opening_character_positions",
    "opening_time_state",
    "transition_mode",
}


def _coerce_novel_pk(novel_id: str | int | None) -> int | None:
    try:
        if novel_id is None:
            return None
        return int(novel_id)
    except (TypeError, ValueError):
        return None


def _truncate_text(value: Any, max_chars: int) -> str:
    return str(value or "")[:max_chars]


def _compact_jsonable(
    value: Any,
    *,
    max_chars: int = 220,
    max_items: int = 6,
    depth: int = 0,
    max_depth: int = 4,
) -> Any:
    if depth >= max_depth:
        return _truncate_text(value, max_chars)
    if isinstance(value, dict):
        items = list(value.items())[: max(max_items, 1)]
        return {
            str(key): _compact_jsonable(
                item,
                max_chars=max_chars,
                max_items=max_items,
                depth=depth + 1,
                max_depth=max_depth,
            )
            for key, item in items
            if item not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [
            _compact_jsonable(
                item,
                max_chars=max_chars,
                max_items=max_items,
                depth=depth + 1,
                max_depth=max_depth,
            )
            for item in value[: max(max_items, 1)]
            if item not in (None, "", [], {})
        ]
    if isinstance(value, str):
        return value[:max_chars]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _truncate_text(value, max_chars)


def _build_reviewer_context_json(context: dict[str, Any], *, reviewer_kind: str) -> str:
    if reviewer_kind == "progression":
        keys = [
            "outline_contract",
            "recent_advancement_window",
            "previous_transition_state",
            "current_volume_arc_state",
            "book_progression_state",
            "anti_repeat_constraints",
            "transition_constraints",
            "thread_ledger",
        ]
        max_chars = 180
        max_items = 6
    else:
        keys = [
            "outline_contract",
            "thread_ledger",
            "previous_transition_state",
            "transition_constraints",
            "anti_repeat_constraints",
            "story_bible_context",
            "character_states",
            "summaries",
            "memory_governance_notes",
            "constraint_usage_notes",
            "context_selector_meta",
        ]
        max_chars = 180
        max_items = 5
    subset = {
        key: _compact_jsonable(
            context.get(key),
            max_chars=max_chars,
            max_items=max_items,
        )
        for key in keys
        if key in context and context.get(key) not in (None, "", [], {})
    }
    return json.dumps(subset, ensure_ascii=False)


def _timed_invoke(llm: Any, prompt: str, op: str, meta: dict[str, Any] | None = None):
    last_exc: Exception | None = None
    for attempt in range(_AGENT_MAX_RETRIES):
        started = time.perf_counter()
        try:
            resp = llm.invoke(prompt)
            elapsed = time.perf_counter() - started
            logger.info(
                "LLM invoke success op=%s elapsed=%.2fs prompt_chars=%s attempt=%s meta=%s",
                op,
                elapsed,
                len(prompt),
                attempt + 1,
                meta or {},
            )
            return resp
        except Exception as exc:
            elapsed = time.perf_counter() - started
            last_exc = exc
            if not _is_retryable(exc) or attempt >= _AGENT_MAX_RETRIES - 1:
                logger.exception(
                    "LLM invoke failed op=%s elapsed=%.2fs prompt_chars=%s attempt=%s meta=%s",
                    op,
                    elapsed,
                    len(prompt),
                    attempt + 1,
                    meta or {},
                )
                raise
            delay = _AGENT_RETRY_BACKOFF[attempt] if attempt < len(_AGENT_RETRY_BACKOFF) else 10.0
            logger.warning(
                "LLM invoke transient error op=%s attempt=%s error=%s delay=%.1fs",
                op,
                attempt + 1,
                type(exc).__name__,
                delay,
            )
            time.sleep(delay)
    raise last_exc  # type: ignore[misc]


def _parse_json_response(content: str) -> dict:
    """Parse JSON from LLM response, handling markdown code blocks."""
    content = content.strip()
    # Remove markdown code blocks if present
    if content.startswith("```"):
        lines = content.split("\n")
        # Remove first line (```json or ```)
        lines = lines[1:]
        # Remove last line (```)
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse JSON response: {e}")
        return {"raw": content}


def _extract_json_block(content: str) -> str:
    """Extract first JSON object/array from noisy LLM responses."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    if text.startswith("{") or text.startswith("["):
        return text
    match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.DOTALL)
    return match.group(1) if match else text


def _invoke_json_with_schema(
    llm: Any,
    prompt: str,
    schema_cls: type[BaseModel],
    retries: int = 3,
    *,
    stage: str = "structured",
    strict: bool = False,
    provider: str | None = None,
    model: str | None = None,
    chapter_num: int | None = None,
    prompt_template: str | None = None,
    prompt_version: str = "v2",
) -> dict:
    """Enforce structured output with retry on parse/validation and transient API errors."""
    prompt_hash = hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()[:16]
    template_name = str(prompt_template or stage)
    version = str(prompt_version or "v2")
    parser = PydanticOutputParser(pydantic_object=schema_cls)
    format_instructions = parser.get_format_instructions()
    prompt = render_prompt(
        "structured_output_enforcer",
        base_prompt=prompt,
        format_instructions=format_instructions,
    )
    error = ""
    last_error_code = "MODEL_OUTPUT_PARSE_FAILED"
    for attempt in range(retries + 1):
        try:
            started = time.perf_counter()
            resp = llm.invoke(prompt)
            elapsed = time.perf_counter() - started
            logger.info(
                "LLM structured invoke success stage=%s schema=%s attempt=%s elapsed=%.2fs prompt_chars=%s prompt_hash=%s",
                stage,
                schema_cls.__name__,
                attempt + 1,
                elapsed,
                len(prompt),
                prompt_hash,
            )
            logger.info(
                "LLM structured prompt meta stage=%s template=%s version=%s prompt_hash=%s",
                stage,
                template_name,
                version,
                prompt_hash,
            )
            provider_block = extract_provider_block(resp)
            if provider_block:
                reason = str(provider_block.get("reason") or "").strip()
                message = str(provider_block.get("message") or "").strip()
                error = f"provider blocked response reason={reason or 'unknown'}"
                if message:
                    error = f"{error} message={message}"
                last_error_code = "MODEL_PROVIDER_BLOCKED"
                delay = _AGENT_RETRY_BACKOFF[attempt] if attempt < len(_AGENT_RETRY_BACKOFF) else 10.0
                logger.warning(
                    "LLM structured provider block stage=%s schema=%s attempt=%s delay=%.1fs prompt_hash=%s reason=%s message=%s",
                    stage,
                    schema_cls.__name__,
                    attempt + 1,
                    delay,
                    prompt_hash,
                    reason,
                    message,
                )
                if attempt < retries:
                    time.sleep(delay)
                continue
            content = response_to_text(resp).strip()
            if not content:
                delay = _AGENT_RETRY_BACKOFF[attempt] if attempt < len(_AGENT_RETRY_BACKOFF) else 10.0
                logger.warning(
                    "LLM structured empty response stage=%s schema=%s attempt=%s delay=%.1fs prompt_hash=%s",
                    stage,
                    schema_cls.__name__,
                    attempt + 1,
                    delay,
                    prompt_hash,
                )
                error = "empty response from model"
                last_error_code = "MODEL_OUTPUT_PARSE_FAILED"
                if attempt < retries:
                    time.sleep(delay)
                continue
            try:
                parsed = parser.parse(content)
                return parsed.model_dump()
            except Exception:
                payload = _extract_json_block(content)
                raw = json.loads(payload)
                validated = schema_cls.model_validate(raw)
                return validated.model_dump()
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error_code = "MODEL_OUTPUT_SCHEMA_INVALID" if isinstance(exc, ValidationError) else "MODEL_OUTPUT_PARSE_FAILED"
            error = str(exc)
            logger.warning(
                "LLM structured parse retry stage=%s schema=%s attempt=%s error=%s prompt_hash=%s",
                stage,
                schema_cls.__name__,
                attempt + 1,
                error,
                prompt_hash,
            )
            prompt = render_prompt(
                "structured_output_retry_suffix",
                base_prompt=prompt,
                error=error,
            )
        except Exception as exc:
            error = str(exc)
            if _is_retryable(exc) and attempt < retries:
                delay = _AGENT_RETRY_BACKOFF[attempt] if attempt < len(_AGENT_RETRY_BACKOFF) else 10.0
                logger.warning(
                    "LLM structured invoke transient error stage=%s schema=%s attempt=%s error=%s delay=%.1fs prompt_hash=%s",
                    stage,
                    schema_cls.__name__,
                    attempt + 1,
                    type(exc).__name__,
                    delay,
                    prompt_hash,
                )
                time.sleep(delay)
            else:
                last_error_code = "MODEL_OUTPUT_CONTRACT_EXHAUSTED"
                break
    logger.warning(
        "Structured output failed after retries stage=%s schema=%s error=%s prompt_hash=%s",
        stage,
        schema_cls.__name__,
        error,
        prompt_hash,
    )
    if strict:
        raise OutputContractError(
            code=last_error_code,
            stage=stage,
            chapter_num=chapter_num,
            provider=provider,
            model=model,
                detail=(
                    error
                    or f"{schema_cls.__name__} structured_output_failed"
                    + f" template={template_name} version={version} prompt_hash={prompt_hash}"
                ),
                retryable=True,
            )
    return {"raw": "structured_output_fallback", "error": error}


class OutlineItemSchema(BaseModel):
    title: str | None = None
    outline: str | None = None
    role: str | None = None
    purpose: str | None = None
    suspense_level: str | None = None
    foreshadowing: str | None = None
    plot_twist_level: str | None = None
    hook: str | None = None
    payoff: str | None = None
    mini_climax: str | None = None
    summary: str | None = None
    chapter_objective: str | None = None
    required_new_information: list[str] = Field(default_factory=list)
    required_irreversible_change: str | None = None
    relationship_delta: str | None = None
    conflict_axis: str | None = None
    payoff_kind: str | None = None
    reveal_kind: str | None = None
    forbidden_repeats: list[str] = Field(default_factory=list)
    opening_scene: str | None = None
    opening_character_positions: list[str] = Field(default_factory=list)
    opening_time_state: str | None = None
    transition_mode: str | None = None


class FullOutlinesSchema(BaseModel):
    outlines: list[OutlineItemSchema]


# Alias: batch generation uses the same shape as the legacy full-book schema.
VolumeOutlineBatchSchema = FullOutlinesSchema


class VolumeOutlineHarmonizationItemSchema(BaseModel):
    chapter_num: int
    payoff: str | None = None
    reveal_kind: str | None = None
    forbidden_repeats: list[str] = Field(default_factory=list)
    opening_scene: str | None = None
    opening_character_positions: list[str] = Field(default_factory=list)
    opening_time_state: str | None = None
    transition_mode: str | None = None


class VolumeOutlineHarmonizationSchema(BaseModel):
    outlines: list[VolumeOutlineHarmonizationItemSchema]


class ReviewSchema(BaseModel):
    score: float = 0.8
    feedback: str = ""


class ReviewIssueSchema(BaseModel):
    category: str = ""
    severity: str = "should_fix"
    claim: str = ""
    evidence: str = ""
    confidence: float = 0.6


class ReviewScorecardSchema(BaseModel):
    score: float = 0.8
    confidence: float = 0.75
    feedback: str = ""
    positives: list[str] = []
    must_fix: list[ReviewIssueSchema] = []
    should_fix: list[ReviewIssueSchema] = []
    risks: list[str] = []


class FactualReviewSchema(BaseModel):
    score: float = 0.8
    feedback: str = ""
    contradictions: list[str] = []


class CrossChapterContradictionSchema(BaseModel):
    category: str = "character"
    severity: str = "should_fix"
    claim: str = ""
    evidence: str = ""
    confidence: float = 0.7


class CrossChapterCheckSchema(BaseModel):
    contradictions: list[CrossChapterContradictionSchema] = []


class UnknownCharacterVerdictSchema(BaseModel):
    name: str = ""
    result: str = "minor"  # unreasonable | new_reasonable | minor
    reason: str = ""
    evidence: str = ""
    confidence: float = 0.7


class UnknownCharacterCheckSchema(BaseModel):
    verdicts: list[UnknownCharacterVerdictSchema] = []


class AestheticReviewSchema(BaseModel):
    score: float = 0.8
    feedback: str = ""
    highlights: list[str] = []


class FinalBookReviewSchema(BaseModel):
    score: float = 0.8
    feedback: str = "ok"
    confidence: float = 0.75
    must_fix: list[str] = Field(default_factory=list)
    should_improve: list[str] = Field(default_factory=list)
    fallback: bool = False


class FactExtractionSchema(BaseModel):
    events: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []


class CharacterSpecSchema(BaseModel):
    name: str = ""
    goal: str = ""
    conflict: str = ""


class PlotlineSpecSchema(BaseModel):
    id: str = ""
    name: str = ""
    start: int = 1
    end: int = 1


class ForeshadowingSpecSchema(BaseModel):
    id: str = ""
    plant_chapter: int = 1
    resolve_chapter: int = 1
    content: str = ""


class VolumeMapSpecSchema(BaseModel):
    volume: int = 1
    chapter_range: str = ""
    main_goal: str = ""


class ClimaxPlanSpecSchema(BaseModel):
    chapter_range: str = ""
    level: str = "small"
    objective: str = ""


class ConstitutionSchema(BaseModel):
    core_values: list[str] = Field(default_factory=list)
    quality_standards: list[str] = Field(default_factory=list)
    style_rules: list[str] = Field(default_factory=list)
    red_lines: list[str] = Field(default_factory=list)


class StorySpecificationSchema(BaseModel):
    logline: str = ""
    theme: str = ""
    target_reader: str = ""
    characters: list[CharacterSpecSchema] = Field(default_factory=list)
    worldview: str = ""
    main_conflict: str = ""
    sub_conflicts: list[str] = Field(default_factory=list)
    plotlines: list[PlotlineSpecSchema] = Field(default_factory=list)
    foreshadowing: list[ForeshadowingSpecSchema] = Field(default_factory=list)
    volume_map: list[VolumeMapSpecSchema] = Field(default_factory=list)
    climax_plan: list[ClimaxPlanSpecSchema] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class CreativePlanVolumeSchema(BaseModel):
    volume: int = 1
    chapter_range: str = ""
    goal: str = ""


class EmotionCurveSchema(BaseModel):
    chapter: int = 1
    type: str = ""
    intensity: str = ""


class SerialRulesSchema(BaseModel):
    small_climax_every: str = "3-5 chapters"
    medium_climax_every: str = "10-15 chapters"
    volume_climax_every: str = "30-50 chapters"


class CreativePlanSchema(BaseModel):
    writing_method: str = ""
    volume_plan: list[CreativePlanVolumeSchema] = Field(default_factory=list)
    emotion_curve: list[EmotionCurveSchema] = Field(default_factory=list)
    pacing_rules: list[str] = Field(default_factory=list)
    opening_strategy: str = ""
    serial_rules: SerialRulesSchema = Field(default_factory=SerialRulesSchema)
    risk_controls: list[str] = Field(default_factory=list)


class TaskItemSchema(BaseModel):
    task_id: str = ""
    chapter_num: int = 1
    must_contain: list[str] = Field(default_factory=list)
    plotlines: list[str] = Field(default_factory=list)
    foreshadowing: list[str] = Field(default_factory=list)
    hook_type: str = ""
    climax_level: str = "none"
    dependencies: list[str] = Field(default_factory=list)


class TasksBreakdownSchema(BaseModel):
    tasks: list[TaskItemSchema] = Field(default_factory=list)


class CharacterStateUpdateSchema(BaseModel):
    name: str = ""
    status: str = "unknown"
    location: str = ""
    new_items: list[str] = Field(default_factory=list)
    lost_items: list[str] = Field(default_factory=list)
    injuries: list[str] = Field(default_factory=list)
    can_use_both_hands: bool = True
    limitations: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    relationship_changes: list[str] = Field(default_factory=list)
    key_action: str = ""
    realm: str = ""
    emotional_state: str = ""


class CharacterStateUpdatesSchema(BaseModel):
    updates: list[CharacterStateUpdateSchema] = Field(default_factory=list)


class CharacterRelationSchema(BaseModel):
    source: str
    target: str
    relation_type: str = ""
    description: str = ""
    sentiment: str = "neutral"


class CharacterRelationsSchema(BaseModel):
    relations: list[CharacterRelationSchema] = []


class ChapterAdvancementMemorySchema(BaseModel):
    chapter_objective: str = ""
    actual_progress: str = ""
    new_information: list[str] = Field(default_factory=list)
    irreversible_change: str = ""
    relationship_delta: str = ""
    conflict_axis: str = ""
    payoff_kind: str = ""
    reveal_kind: str = ""
    resolved_threads: list[str] = Field(default_factory=list)
    new_unresolved_threads: list[str] = Field(default_factory=list)
    forbidden_repeats: list[str] = Field(default_factory=list)
    phase: str = ""
    major_beats: list[str] = Field(default_factory=list)


class ChapterTransitionMemorySchema(BaseModel):
    ending_scene: str = ""
    character_positions: list[str] = Field(default_factory=list)
    physical_state: str = ""
    last_action: str = ""
    time_state: str = ""
    scene_exit: str = ""
    unresolved_exit: str = ""


class ChapterMemoryExtractionSchema(BaseModel):
    advancement: ChapterAdvancementMemorySchema = Field(default_factory=ChapterAdvancementMemorySchema)
    transition: ChapterTransitionMemorySchema = Field(default_factory=ChapterTransitionMemorySchema)
    advancement_confidence: float = 0.0
    transition_confidence: float = 0.0
    validation_notes: list[str] = Field(default_factory=list)


class ProgressionReviewSchema(BaseModel):
    score: float = 0.8
    confidence: float = 0.75
    feedback: str = ""
    positives: list[str] = []
    must_fix: list[ReviewIssueSchema] = []
    should_fix: list[ReviewIssueSchema] = []
    risks: list[str] = []
    duplicate_beats: list[str] = []
    no_new_delta: list[str] = []
    repeated_reveal: list[str] = []
    repeated_relationship_turn: list[str] = []
    transition_conflict: list[str] = []


class FactualSubReviewSchema(ReviewScorecardSchema):
    """Factual sub-review within combined mode — adds explicit contradictions list."""
    contradictions: list[str] = Field(default_factory=list)


class AiFlavorSchema(BaseModel):
    score: int = 5
    issues: list[str] = Field(default_factory=list)


class WebnovelPrinciplesSchema(BaseModel):
    score: int = 5
    violations: list[str] = Field(default_factory=list)


class ReviewCombinedSchema(BaseModel):
    """Single-call combined review replacing 6 separate reviewer calls."""
    structure: ReviewScorecardSchema = Field(default_factory=ReviewScorecardSchema)
    factual: FactualSubReviewSchema = Field(default_factory=FactualSubReviewSchema)
    progression: ProgressionReviewSchema = Field(default_factory=ProgressionReviewSchema)
    aesthetic: ReviewScorecardSchema = Field(default_factory=ReviewScorecardSchema)
    ai_flavor: AiFlavorSchema = Field(default_factory=AiFlavorSchema)
    webnovel_principles: WebnovelPrinciplesSchema = Field(default_factory=WebnovelPrinciplesSchema)


def _normalize_outline_item(item: dict[str, Any], chapter_no: int) -> dict[str, Any]:
    return normalize_outline_contract(
        {
            "chapter_num": chapter_no,
            "title": normalize_title_text(item.get("title")) or f"第{chapter_no}章",
            "outline": item.get("outline", ""),
            "role": item.get("role"),
            "purpose": item.get("purpose"),
            "suspense_level": item.get("suspense_level"),
            "foreshadowing": item.get("foreshadowing"),
            "plot_twist_level": item.get("plot_twist_level"),
            "hook": item.get("hook"),
            "payoff": item.get("payoff"),
            "mini_climax": item.get("mini_climax"),
            "summary": item.get("summary"),
            "chapter_objective": item.get("chapter_objective"),
            "required_new_information": item.get("required_new_information"),
            "required_irreversible_change": item.get("required_irreversible_change"),
            "relationship_delta": item.get("relationship_delta"),
            "conflict_axis": item.get("conflict_axis"),
            "payoff_kind": item.get("payoff_kind"),
            "reveal_kind": item.get("reveal_kind"),
            "forbidden_repeats": item.get("forbidden_repeats"),
            "opening_scene": item.get("opening_scene"),
            "opening_character_positions": item.get("opening_character_positions"),
            "opening_time_state": item.get("opening_time_state"),
            "transition_mode": item.get("transition_mode"),
        },
        chapter_no,
    )


def _trim_fact_lists(data: dict[str, Any]) -> dict[str, Any]:
    trimmed = dict(data)
    trimmed["events"] = list((data.get("events") or []))[:12]
    trimmed["entities"] = list((data.get("entities") or []))[:12]
    trimmed["facts"] = list((data.get("facts") or []))[:16]
    return trimmed


def _trim_text_list(value: Any, *, max_items: int, max_chars: int = 180) -> list[str]:
    items = value if isinstance(value, list) else []
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        clipped = text[:max_chars]
        if clipped not in out:
            out.append(clipped)
        if len(out) >= max_items:
            break
    return out


def _trim_progression_memory_payload(data: dict[str, Any]) -> dict[str, Any]:
    trimmed = dict(data)
    advancement = dict(data.get("advancement") or {}) if isinstance(data.get("advancement"), dict) else {}
    transition = dict(data.get("transition") or {}) if isinstance(data.get("transition"), dict) else {}
    advancement["new_information"] = _trim_text_list(advancement.get("new_information"), max_items=5)
    advancement["resolved_threads"] = _trim_text_list(advancement.get("resolved_threads"), max_items=5)
    advancement["new_unresolved_threads"] = _trim_text_list(advancement.get("new_unresolved_threads"), max_items=5)
    advancement["forbidden_repeats"] = _trim_text_list(advancement.get("forbidden_repeats"), max_items=5)
    advancement["major_beats"] = _trim_text_list(advancement.get("major_beats"), max_items=5)
    transition["character_positions"] = _trim_text_list(transition.get("character_positions"), max_items=5)
    trimmed["advancement"] = advancement
    trimmed["transition"] = transition
    trimmed["validation_notes"] = _trim_text_list(data.get("validation_notes"), max_items=6)
    return trimmed


def _compact_final_review_chapters(
    chapters: list[dict[str, Any]],
    *,
    direct_limit: int = 18,
    window_size: int = 8,
) -> list[dict[str, Any]]:
    compacted = [
        {
            "chapter_num": item.get("chapter_num"),
            "title": _truncate_text(item.get("title"), 80),
            "summary": _truncate_text(item.get("summary") or item.get("content"), 220),
        }
        for item in chapters
        if isinstance(item, dict) and item.get("chapter_num") is not None
    ]
    if len(compacted) <= direct_limit:
        return compacted

    grouped: list[dict[str, Any]] = []
    for idx in range(0, len(compacted), max(window_size, 1)):
        window = compacted[idx : idx + max(window_size, 1)]
        if not window:
            continue
        start = window[0].get("chapter_num")
        end = window[-1].get("chapter_num")
        grouped.append(
            {
                "chapter_range": f"{start}-{end}",
                "summary": " | ".join(
                    f"ch{entry.get('chapter_num')}: {entry.get('summary') or entry.get('title') or ''}"
                    for entry in window
                    if entry.get("chapter_num") is not None
                )[:1200],
            }
        )
    return grouped[: max(direct_limit, 1)]


def _final_book_review_fallback(reason: str) -> dict[str, Any]:
    fallback_reason = str(reason or "unknown_error")[:240]
    return {
        "score": 0.45,
        "confidence": 0.2,
        "feedback": "全书终审未能稳定完成，已降级为告警结果。",
        "must_fix": [f"final_book_review_unavailable: {fallback_reason}"],
        "should_improve": [],
        "fallback": True,
    }


def _default_prewrite_component(key: str, novel: dict[str, Any], num_chapters: int) -> dict[str, Any]:
    if key == "constitution":
        return ConstitutionSchema(
            core_values=["核心爽点清晰", "主线推进稳定"],
            quality_standards=["章节必须有明确推进", "人物行为前后一致", "章末保留阅读钩子"],
            style_rules=["保持题材既定文风", "避免重复解释同一信息"],
            red_lines=["不得自相矛盾", "不得无故跳过关键兑现"],
        ).model_dump()
    if key == "specification":
        title = str((novel or {}).get("title") or "未命名")
        return StorySpecificationSchema(
            logline=title,
            theme=str((novel or {}).get("style") or "成长与冲突"),
            target_reader=str((novel or {}).get("audience") or "网文读者"),
            characters=[CharacterSpecSchema(name="主角", goal="推动主线", conflict="解决核心冲突")],
            worldview=str((novel or {}).get("genre") or "现实向世界观"),
            main_conflict=str((novel or {}).get("user_idea") or "围绕核心创意展开冲突"),
            volume_map=[VolumeMapSpecSchema(volume=1, chapter_range=f"1-{max(1, min(num_chapters, 30))}", main_goal="建立主线冲突")],
            climax_plan=[ClimaxPlanSpecSchema(chapter_range=f"1-{max(1, min(num_chapters, 5))}", level="small", objective="建立开篇冲突")],
            constraints=["保持人物目标清晰", "每章必须新增推进"],
        ).model_dump()
    if key == "creative_plan":
        return CreativePlanSchema(
            writing_method=str((novel or {}).get("writing_method") or "三幕结构"),
            volume_plan=[CreativePlanVolumeSchema(volume=1, chapter_range=f"1-{max(1, min(num_chapters, 30))}", goal="推进主线")],
            pacing_rules=["每3-5章形成小高潮", "避免连续章节原地踏步"],
            opening_strategy="前三章快速建立冲突和钩子",
            risk_controls=["发现重复推进时及时收束"],
        ).model_dump()
    if key == "tasks":
        return TasksBreakdownSchema(
            tasks=[
                TaskItemSchema(
                    task_id="T001",
                    chapter_num=1,
                    must_contain=["建立主角目标", "抛出主线冲突"],
                    hook_type="悬念",
                    climax_level="small",
                )
            ]
        ).model_dump()
    return {"raw": f"fallback-{key}"}


def _normalize_prewrite_component(
    key: str,
    data: dict[str, Any] | None,
    *,
    novel: dict[str, Any],
    num_chapters: int,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        return _default_prewrite_component(key, novel, num_chapters)
    if key == "constitution":
        return ConstitutionSchema.model_validate(data).model_dump()
    if key == "specification":
        normalized = StorySpecificationSchema.model_validate(data).model_dump()
        if not normalized.get("characters"):
            normalized["characters"] = _default_prewrite_component(key, novel, num_chapters)["characters"]
        return normalized
    if key == "creative_plan":
        normalized = CreativePlanSchema.model_validate(data).model_dump()
        if not normalized.get("volume_plan"):
            normalized["volume_plan"] = _default_prewrite_component(key, novel, num_chapters)["volume_plan"]
        return normalized
    if key == "tasks":
        normalized = TasksBreakdownSchema.model_validate(data).model_dump()
        if not normalized.get("tasks"):
            normalized["tasks"] = _default_prewrite_component(key, novel, num_chapters)["tasks"]
        return normalized
    return data


class ArchitectAgent:
    """High-level story architecture planning."""

    def run(
        self,
        novel_id: str,
        chapter_num: int,
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
    ) -> dict:
        llm = get_llm_with_fallback(provider, model)
        prompt = render_prompt("plot_architecture", novel_id=novel_id, chapter_num=chapter_num)
        try:
            response = _timed_invoke(
                llm,
                prompt,
                "architect",
                {"novel_id": novel_id, "chapter_num": chapter_num, "provider": provider, "model": model},
            )
            result = _parse_json_response(response.content)
            logger.info(f"ArchitectAgent completed for novel {novel_id} chapter {chapter_num}")
            return result
        except Exception as e:
            logger.error(f"ArchitectAgent failed: {e}")
            return {"summary": f"Chapter {chapter_num} architecture", "plan": "auto-generated"}


class PrewritePlannerAgent:
    """Generate novel-writer style prewrite artifacts: constitution/spec/plan/tasks."""

    def run(
        self,
        novel: dict,
        num_chapters: int,
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
        strategy_key: str | None = None,
        novel_config: dict[str, Any] | None = None,
    ) -> dict:
        result: dict[str, Any] = {}
        templates = [
            ("constitution", "constitution"),
            ("specification", "specify_story"),
            ("creative_plan", "plan_story"),
            ("tasks", "tasks_breakdown"),
        ]
        schema_map: dict[str, type[BaseModel]] = {
            "constitution": ConstitutionSchema,
            "specification": StorySpecificationSchema,
            "creative_plan": CreativePlanSchema,
            "tasks": TasksBreakdownSchema,
        }
        started_all = time.perf_counter()
        for key, tpl in templates:
            try:
                asset_key = {
                    "constitution": "prewrite.constitution",
                    "specification": "prewrite.specification",
                    "creative_plan": "prewrite.storyline",
                    "tasks": "prewrite.blueprint",
                }[key]
                resolved = resolve_ai_profile(
                    strategy_key,
                    asset_key,
                    novel_config=novel_config,
                    runtime_override={"provider": provider, "model": model},
                )
                llm = get_llm_with_fallback(
                    resolved["provider"],
                    resolved["model"],
                    inference=resolved["inference"],
                )
                prompt = render_prompt(
                    tpl,
                    novel=novel,
                    num_chapters=num_chapters,
                    language=language,
                )
                parsed = _invoke_json_with_schema(
                    llm,
                    prompt,
                    schema_map[key],
                    strict=False,
                    stage=f"prewrite.{key}",
                    provider=resolved["provider"],
                    model=resolved["model"],
                    prompt_template=tpl,
                    prompt_version="v1",
                )
                result[key] = _normalize_prewrite_component(
                    key,
                    parsed if isinstance(parsed, dict) and "raw" not in parsed else None,
                    novel=novel,
                    num_chapters=num_chapters,
                )
                logger.info(
                    "PrewritePlannerAgent normalized key=%s provider=%s model=%s resolution_trace=%s",
                    key,
                    resolved["provider"],
                    resolved["model"],
                    resolved["resolution_trace"],
                )
            except Exception as e:
                logger.error(f"PrewritePlannerAgent {key} failed: {e}")
                result[key] = _default_prewrite_component(key, novel, num_chapters)
        logger.info("PrewritePlannerAgent finished elapsed=%.2fs", time.perf_counter() - started_all)
        return result


class OutlinerAgent:
    """Chapter outline generation."""

    def run(
        self,
        novel_id: str,
        chapter_num: int,
        plan: dict,
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
    ) -> dict:
        llm = get_llm_with_fallback(provider, model)
        prompt = render_prompt("chapter_blueprint", novel_id=novel_id, chapter_num=chapter_num, plan=plan)
        try:
            response = _timed_invoke(
                llm,
                prompt,
                "outliner.single",
                {"novel_id": novel_id, "chapter_num": chapter_num, "provider": provider, "model": model},
            )
            result = normalize_outline_contract(_parse_json_response(response.content), chapter_num)
            title = normalize_title_text(result.get("title"))
            result["title"] = title or f"Chapter {chapter_num}"
            logger.info(f"OutlinerAgent completed for novel {novel_id} chapter {chapter_num}")
            return result
        except Exception as e:
            logger.error(f"OutlinerAgent failed: {e}")
            return normalize_outline_contract(
                {
                    "title": f"Chapter {chapter_num}",
                    "outline": "",
                    "purpose": "推进主线",
                },
                chapter_num,
            )

    def run_volume_outlines(
        self,
        novel_id: str,
        volume_no: int,
        start_chapter: int,
        num_chapters: int,
        prewrite: dict,
        novel_version_id: int | None = None,
        previous_summaries: list[dict] | None = None,
        planning_context: dict[str, Any] | None = None,
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
        strategy_key: str | None = None,
        novel_config: dict[str, Any] | None = None,
    ) -> list[dict]:
        """Generate outlines for one volume via smaller fixed-size batches."""
        end_chapter = start_chapter + num_chapters - 1
        previous_summaries = previous_summaries or []
        planning_context = planning_context or {}
        novel_pk = _coerce_novel_pk(novel_id)
        existing_map: dict[int, dict[str, Any]] = {}
        if novel_version_id is not None and novel_pk is not None:
            existing = load_outlines_from_db(novel_pk, novel_version_id)
            existing_map = {
                int(item.get("chapter_num") or 0): item
                for item in existing
                if isinstance(item, dict) and int(item.get("chapter_num") or 0) >= start_chapter
            }

        aggregated: list[dict[str, Any]] = []
        for batch_start in range(start_chapter, end_chapter + 1, _OUTLINE_BATCH_SIZE):
            batch_end = min(batch_start + _OUTLINE_BATCH_SIZE - 1, end_chapter)
            batch_size = batch_end - batch_start + 1
            batch_numbers = range(batch_start, batch_end + 1)
            if existing_map and all(is_outline_content_valid(existing_map.get(ch, {})) for ch in batch_numbers):
                batch_outlines = [
                    _normalize_outline_item(existing_map[ch], ch)
                    for ch in batch_numbers
                ]
                aggregated.extend(batch_outlines)
                logger.info(
                    "OutlinerAgent.run_volume_outlines reused batch vol=%s ch=%s-%s",
                    volume_no,
                    batch_start,
                    batch_end,
                )
                continue

            batch_outlines = self._generate_outline_batch(
                novel_id=novel_id,
                volume_no=volume_no,
                start_chapter=batch_start,
                num_chapters=batch_size,
                prewrite=prewrite,
                previous_summaries=previous_summaries,
                planning_context=planning_context,
                language=language,
                provider=provider,
                model=model,
                strategy_key=strategy_key,
                novel_config=novel_config,
            )
            aggregated.extend(batch_outlines)
            if novel_version_id is not None and novel_pk is not None:
                save_full_outlines(
                    novel_pk,
                    batch_outlines,
                    novel_version_id=novel_version_id,
                    replace_all=False,
                )
            logger.info(
                "OutlinerAgent.run_volume_outlines generated batch vol=%s ch=%s-%s expected_items=%s actual_items=%s persisted=%s",
                volume_no,
                batch_start,
                batch_end,
                batch_size,
                len(batch_outlines),
                novel_version_id is not None and novel_pk is not None,
            )

        try:
            harmonized = self._harmonize_volume_outlines(
                novel_id=novel_id,
                volume_no=volume_no,
                outlines=aggregated,
                prewrite=prewrite,
                previous_summaries=previous_summaries,
                planning_context=planning_context,
                language=language,
                provider=provider,
                model=model,
                strategy_key=strategy_key,
                novel_config=novel_config,
            )
            if harmonized != aggregated and novel_version_id is not None and novel_pk is not None:
                save_full_outlines(
                    novel_pk,
                    harmonized,
                    novel_version_id=novel_version_id,
                    replace_all=False,
                )
            logger.info(
                "OutlinerAgent.run_volume_outlines harmonized vol=%s ch=%s-%s",
                volume_no,
                start_chapter,
                end_chapter,
            )
            return harmonized
        except Exception as exc:
            logger.warning(
                "OutlinerAgent.run_volume_outlines harmonization soft-failed vol=%s ch=%s-%s error=%s",
                volume_no,
                start_chapter,
                end_chapter,
                exc,
            )
            return aggregated

    def _generate_outline_batch(
        self,
        *,
        novel_id: str,
        volume_no: int,
        start_chapter: int,
        num_chapters: int,
        prewrite: dict,
        previous_summaries: list[dict],
        planning_context: dict[str, Any],
        language: str,
        provider: str | None,
        model: str | None,
        strategy_key: str | None = None,
        novel_config: dict[str, Any] | None = None,
    ) -> list[dict]:
        resolved = resolve_ai_profile(
            strategy_key,
            "outline.batch",
            novel_config=novel_config,
            runtime_override={"provider": provider, "model": model},
        )
        llm = get_llm_with_fallback(resolved["provider"], resolved["model"], inference=resolved["inference"])
        end_chapter = start_chapter + num_chapters - 1
        base_prompt = render_prompt(
            "volume_outline_batch",
            novel_id=novel_id,
            volume_no=volume_no,
            start_chapter=start_chapter,
            end_chapter=end_chapter,
            num_chapters=num_chapters,
            prewrite=prewrite,
            previous_summaries=previous_summaries,
            planning_context=planning_context,
            language=language,
        )

        outlines: list[Any] = []
        for attempt in range(3):
            prompt = base_prompt
            if attempt > 0:
                actual = len(outlines) if isinstance(outlines, list) else 0
                prompt = (
                    base_prompt
                    + f"\n\n[重要] 上次只返回了 {actual} 条，必须严格返回"
                    f"第 {start_chapter} 到第 {end_chapter} 章共 {num_chapters} 条，不多不少。"
                )
            data = _invoke_json_with_schema(
                llm,
                prompt,
                VolumeOutlineBatchSchema,
                provider=resolved["provider"],
                model=resolved["model"],
                stage="outline.batch",
            )
            outlines = data.get("outlines", []) if isinstance(data, dict) else []
            if isinstance(outlines, list) and len(outlines) == num_chapters:
                break
            logger.warning(
                "OutlinerAgent._generate_outline_batch count mismatch attempt=%s ch=%s-%s expected=%s actual=%s",
                attempt + 1,
                start_chapter,
                end_chapter,
                num_chapters,
                len(outlines) if isinstance(outlines, list) else "non-list",
            )

        if not isinstance(outlines, list):
            raise ValueError(
                f"invalid batch outline for ch={start_chapter}-{end_chapter}: non-list response after retries"
            )

        missing = num_chapters - len(outlines)
        if missing > 2:
            raise ValueError(
                f"invalid batch outline count for ch={start_chapter}-{end_chapter}:"
                f" expected={num_chapters} actual={len(outlines)} after retries"
            )

        if missing > 0:
            chapter_map = {
                int(o.get("chapter_num") or 0): o
                for o in outlines
                if isinstance(o, dict) and int(o.get("chapter_num") or 0) > 0
            }
            targets = {
                int(t.get("chapter_num") or 0): t
                for t in (planning_context.get("chapter_targets") or [])
                if isinstance(t, dict)
            }
            for ch in range(start_chapter, end_chapter + 1):
                if ch in chapter_map:
                    continue
                prev = chapter_map.get(ch - 1, {})
                nxt = chapter_map.get(ch + 1, {})
                target = targets.get(ch, {})
                goal = str(target.get("goal") or prev.get("hook") or "延续上章情节，推进主线")
                chapter_map[ch] = {
                    "chapter_num": ch,
                    "title": str(target.get("title") or f"第{ch}章"),
                    "outline": goal,
                    "purpose": goal,
                    "hook": str(nxt.get("opening_scene") or ""),
                    "payoff": "",
                    "foreshadowing": "",
                    "conflict_axis": "",
                    "chapter_objective": goal,
                    "required_irreversible_change": "",
                    "mini_climax": "none",
                    "opening_scene": str(prev.get("hook") or ""),
                    "transition_mode": str(nxt.get("transition_mode") or ""),
                }
                logger.warning(
                    "OutlinerAgent._generate_outline_batch inferred missing ch=%s from neighbors", ch
                )
            outlines = [chapter_map[ch] for ch in range(start_chapter, end_chapter + 1)]

        return [
            _normalize_outline_item(outlines[idx], start_chapter + idx)
            for idx in range(num_chapters)
        ]

    def _harmonize_volume_outlines(
        self,
        *,
        novel_id: str,
        volume_no: int,
        outlines: list[dict[str, Any]],
        prewrite: dict,
        previous_summaries: list[dict],
        planning_context: dict[str, Any],
        language: str,
        provider: str | None,
        model: str | None,
        strategy_key: str | None = None,
        novel_config: dict[str, Any] | None = None,
    ) -> list[dict]:
        if len(outlines) <= 1:
            return outlines
        resolved = resolve_ai_profile(
            strategy_key,
            "outline.harmonize",
            novel_config=novel_config,
            runtime_override={"provider": provider, "model": model},
        )
        llm = get_llm_with_fallback(resolved["provider"], resolved["model"], inference=resolved["inference"])
        prompt = render_prompt(
            "volume_outline_harmonize",
            novel_id=novel_id,
            volume_no=volume_no,
            outlines=outlines,
            prewrite=prewrite,
            previous_summaries=previous_summaries,
            planning_context=planning_context,
            language=language,
        )
        data = _invoke_json_with_schema(
            llm,
            prompt,
            VolumeOutlineHarmonizationSchema,
            provider=resolved["provider"],
            model=resolved["model"],
            stage="outline.harmonize",
        )
        items = data.get("outlines", []) if isinstance(data, dict) else []
        if not isinstance(items, list) or not items:
            raise ValueError("empty harmonization outlines")
        updates = {
            int(item.get("chapter_num") or 0): item
            for item in items
            if isinstance(item, dict) and int(item.get("chapter_num") or 0) > 0
        }
        if not updates:
            raise ValueError("invalid harmonization payload")
        harmonized: list[dict[str, Any]] = []
        for outline in outlines:
            chapter_num = int(outline.get("chapter_num") or 0)
            patch = updates.get(chapter_num)
            if not patch:
                harmonized.append(dict(outline))
                continue
            merged = dict(outline)
            for field in _HARMONIZABLE_OUTLINE_FIELDS:
                if field in patch:
                    merged[field] = patch[field]
            harmonized.append(_normalize_outline_item(merged, chapter_num))
        return harmonized

    def run_full_book(
        self,
        novel_id: str,
        num_chapters: int,
        prewrite: dict,
        planning_context: dict[str, Any] | None = None,
        start_chapter: int = 1,
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
    ) -> list[dict]:
        """Generate full-book chapter outlines before chapter writing.

        .. deprecated::
            Use :meth:`run_volume_outlines` instead.  This method attempts to
            generate all chapters in a single LLM call which reliably fails for
            novels longer than ~30 chapters, especially with local / small LLMs.
            Kept for backward compatibility only.
        """
        llm = get_llm_with_fallback(provider, model)
        start_chapter = max(1, int(start_chapter or 1))
        end_chapter = start_chapter + max(0, int(num_chapters or 0)) - 1
        prompt = render_prompt(
            "chapter_blueprint_all",
            novel_id=novel_id,
            num_chapters=num_chapters,
            start_chapter=start_chapter,
            end_chapter=end_chapter,
            prewrite=prewrite,
            planning_context=planning_context or {},
            language=language,
        )
        try:
            data = _invoke_json_with_schema(llm, prompt, FullOutlinesSchema)
            outlines = data.get("outlines", []) if isinstance(data, dict) else []
            if not isinstance(outlines, list) or not outlines:
                raise ValueError("invalid outlines")
            normalized = []
            for idx in range(1, num_chapters + 1):
                item = outlines[idx - 1] if idx - 1 < len(outlines) else {}
                chapter_no = start_chapter + idx - 1
                normalized.append(
                    normalize_outline_contract(
                        {
                            "chapter_num": chapter_no,
                            "title": normalize_title_text(item.get("title")) or f"第{chapter_no}章",
                            "outline": item.get("outline", ""),
                            "role": item.get("role"),
                            "purpose": item.get("purpose"),
                            "suspense_level": item.get("suspense_level"),
                            "foreshadowing": item.get("foreshadowing"),
                            "plot_twist_level": item.get("plot_twist_level"),
                            "hook": item.get("hook"),
                            "payoff": item.get("payoff"),
                            "mini_climax": item.get("mini_climax"),
                            "summary": item.get("summary"),
                            "chapter_objective": item.get("chapter_objective"),
                            "required_new_information": item.get("required_new_information"),
                            "required_irreversible_change": item.get("required_irreversible_change"),
                            "relationship_delta": item.get("relationship_delta"),
                            "conflict_axis": item.get("conflict_axis"),
                            "payoff_kind": item.get("payoff_kind"),
                            "reveal_kind": item.get("reveal_kind"),
                            "forbidden_repeats": item.get("forbidden_repeats"),
                            "opening_scene": item.get("opening_scene"),
                            "opening_character_positions": item.get("opening_character_positions"),
                            "opening_time_state": item.get("opening_time_state"),
                            "transition_mode": item.get("transition_mode"),
                        },
                        chapter_no,
                    )
                )
            return normalized
        except Exception as e:
            logger.error(f"OutlinerAgent run_full_book failed: {e}")
            return [
                normalize_outline_contract(
                    {
                        "chapter_num": i,
                        "title": f"第{i}章",
                        "outline": "",
                        "role": "推进剧情",
                        "purpose": "推进主线",
                        "suspense_level": "中",
                        "foreshadowing": "",
                        "plot_twist_level": "低",
                        "hook": "",
                        "payoff": "",
                        "mini_climax": "none",
                        "summary": "",
                    },
                    i,
                )
                for i in range(start_chapter, end_chapter + 1)
            ]


class ContextLoaderAgent:
    """Load relevant context using layered memory (build_chapter_context)."""

    def run(
        self,
        novel_id: int | str,
        novel_version_id: int | None,
        chapter_num: int,
        prewrite: dict | None = None,
        outline: dict | None = None,
        db=None,
    ) -> dict:
        from app.services.memory.context import get_context_for_chapter

        # get_context_for_chapter uses build_chapter_context internally for layered context
        return get_context_for_chapter(
            novel_id,
            chapter_num,
            db=db,
            prewrite=prewrite,
            outline=outline,
            novel_version_id=novel_version_id,
        )


class WriterAgent:
    """Draft chapter content."""

    def run(
        self,
        novel_id: str,
        chapter_num: int,
        outline: dict,
        context: dict,
        language: str = "zh",
        native_style_profile: str = "",
        provider: str | None = None,
        model: str | None = None,
        inference: dict[str, Any] | None = None,
        word_count: int | None = None,
        strategy_key: str | None = None,
        pacing_mode: str | None = None,
        closure_state: dict[str, Any] | None = None,
    ) -> str:
        template = "first_chapter" if chapter_num == 1 else "next_chapter"
        prompt = render_prompt(
            template,
            novel_id=novel_id,
            chapter_num=chapter_num,
            outline=outline,
            context=context,
            language=language,
            native_style_profile=native_style_profile,
            role_section=build_writer_role_section(),
            memory_policy_section=build_memory_governance_sections(include_constraint_usage=True),
            style_overlay_section=build_style_overlay_sections(
                strategy_key=strategy_key,
                native_style_profile=native_style_profile,
                language=language,
                pacing_mode=pacing_mode,
                closure_state=closure_state or (context.get("closure_state") if isinstance(context, dict) else None),
            ),
            **build_chapter_length_prompt_kwargs(word_count),
        )
        try:
            content = invoke_chapter_body_structured(
                prompt=prompt,
                stage="writer",
                chapter_num=chapter_num,
                provider=provider,
                model=model,
                inference=inference,
                prompt_template=template,
                prompt_version="v2",
            )
            logger.info(f"WriterAgent completed for novel {novel_id} chapter {chapter_num}, length: {len(content)}")
            return content
        except OutputContractError:
            logger.exception(
                "WriterAgent contract failed chapter=%s provider=%s model=%s",
                chapter_num,
                provider,
                model,
            )
            raise
        except Exception as e:
            logger.error(f"WriterAgent failed: {e}")
            root = e
            while getattr(root, "__cause__", None) is not None:
                root = root.__cause__
            root_type = type(root).__name__
            root_msg = str(root).strip().replace("\n", " ")
            if len(root_msg) > 240:
                root_msg = root_msg[:240]
            raise RuntimeError(
                f"writer generation failed for chapter={chapter_num} "
                f"provider={provider or '__default__'} model={model or '__default__'} "
                f"cause={root_type}: {root_msg or 'unknown error'}"
            ) from e


class ReviewerAgent:
    """Review draft and return score + feedback."""

    def run_structured(
        self,
        draft: str,
        chapter_num: int = 0,
        language: str = "zh",
        native_style_profile: str = "",
        provider: str | None = None,
        model: str | None = None,
        inference: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        template = "reviewer_structured"
        llm = get_llm_with_fallback(provider, model, inference=inference)
        prompt = render_prompt(
            template,
            chapter_num=chapter_num,
            language=language,
            native_style_profile=(native_style_profile or "默认"),
            draft=(draft[:7000]),
            role_section=build_reviewer_role_section("结构审校角色"),
        )
        result = _invoke_json_with_schema(
            llm,
            prompt,
            ReviewScorecardSchema,
            strict=True,
            stage="reviewer.structured",
            provider=provider,
            model=model,
            chapter_num=chapter_num,
            prompt_template=template,
            prompt_version="v2",
        )
        score = float(result.get("score", 0.8))
        if score > 1:
            score = score / 100 if score <= 100 else 0.8
        result["score"] = max(0.0, min(1.0, score))
        conf = float(result.get("confidence", 0.75))
        result["confidence"] = max(0.0, min(1.0, conf))
        return result

    def run(
        self,
        draft: str,
        chapter_num: int = 0,
        language: str = "zh",
        native_style_profile: str = "",
        provider: str | None = None,
        model: str | None = None,
        inference: dict[str, Any] | None = None,
    ) -> tuple[float, str]:
        result = self.run_structured(
            draft=draft,
            chapter_num=chapter_num,
            language=language,
            native_style_profile=native_style_profile,
            provider=provider,
            model=model,
            inference=inference,
        )
        score = float(result.get("score", 0.8))
        feedback = str(result.get("feedback", ""))
        logger.info(f"ReviewerAgent completed, score: {score}")
        return score, feedback

    def run_factual(
        self,
        draft: str,
        chapter_num: int,
        context: dict,
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
        inference: dict[str, Any] | None = None,
    ) -> tuple[float, str, list[str]]:
        result = self.run_factual_structured(draft, chapter_num, context, language, provider, model, inference=inference)
        return (
            float(result.get("score", 0.75)),
            str(result.get("feedback", "")),
            [str(x) for x in (result.get("contradictions") or [])][:20],
        )

    def run_factual_structured(
        self,
        draft: str,
        chapter_num: int,
        context: dict,
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
        inference: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        template = "reviewer_factual_structured"
        llm = get_llm_with_fallback(provider, model, inference=inference)
        prompt = render_prompt(
            template,
            chapter_num=chapter_num,
            context_json=_build_reviewer_context_json(context, reviewer_kind="factual"),
            draft=(draft[:6000]),
            role_section=build_reviewer_role_section("事实一致性审校角色"),
            memory_policy_section=build_memory_governance_sections(include_constraint_usage=True),
        )
        result = _invoke_json_with_schema(
            llm,
            prompt,
            ReviewScorecardSchema,
            strict=True,
            stage="reviewer.factual",
            provider=provider,
            model=model,
            chapter_num=chapter_num,
            prompt_template=template,
            prompt_version="v2",
        )
        score = float(result.get("score", 0.8))
        if score > 1:
            score = score / 100 if score <= 100 else 0.8
        result["score"] = max(0.0, min(1.0, score))
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.75) or 0.75)))
        contradictions = [str(x.get("claim") or "") for x in (result.get("must_fix") or []) if isinstance(x, dict)]
        result["contradictions"] = [x for x in contradictions if x][:20]
        return result

    def run_progression_structured(
        self,
        draft: str,
        chapter_num: int,
        context: dict,
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
        inference: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        template = "reviewer_progression_structured"
        llm = get_llm_with_fallback(provider, model, inference=inference)
        prompt = render_prompt(
            template,
            chapter_num=chapter_num,
            language=language,
            context_json=_build_reviewer_context_json(context, reviewer_kind="progression"),
            draft=(draft[:6500]),
            role_section=build_reviewer_role_section("推进与连续性审校角色"),
            memory_policy_section=build_memory_governance_sections(include_constraint_usage=True),
        )
        result = _invoke_json_with_schema(
            llm,
            prompt,
            ProgressionReviewSchema,
            strict=True,
            stage="reviewer.progression",
            provider=provider,
            model=model,
            chapter_num=chapter_num,
            prompt_template=template,
            prompt_version="v1",
        )
        score = float(result.get("score", 0.8))
        if score > 1:
            score = score / 100 if score <= 100 else 0.8
        result["score"] = max(0.0, min(1.0, score))
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.75) or 0.75)))
        for field in [
            "duplicate_beats",
            "no_new_delta",
            "repeated_reveal",
            "repeated_relationship_turn",
            "transition_conflict",
        ]:
            result[field] = [str(x)[:180] for x in (result.get(field) or []) if str(x).strip()][:8]
        return result

    def run_aesthetic(
        self,
        draft: str,
        chapter_num: int,
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
        inference: dict[str, Any] | None = None,
    ) -> tuple[float, str, list[str]]:
        result = self.run_aesthetic_structured(draft, chapter_num, language, provider, model, inference=inference)
        return (
            float(result.get("score", 0.75)),
            str(result.get("feedback", "")),
            [str(x) for x in (result.get("highlights") or [])][:20],
        )

    def run_aesthetic_structured(
        self,
        draft: str,
        chapter_num: int,
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
        inference: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        template = "reviewer_aesthetic_structured"
        llm = get_llm_with_fallback(provider, model, inference=inference)
        prompt = render_prompt(
            template,
            chapter_num=chapter_num,
            draft=(draft[:6000]),
            role_section=build_reviewer_role_section("审美审校角色"),
        )
        result = _invoke_json_with_schema(
            llm,
            prompt,
            ReviewScorecardSchema,
            strict=True,
            stage="reviewer.aesthetic",
            provider=provider,
            model=model,
            chapter_num=chapter_num,
            prompt_template=template,
            prompt_version="v2",
        )
        score = float(result.get("score", 0.8))
        if score > 1:
            score = score / 100 if score <= 100 else 0.8
        result["score"] = max(0.0, min(1.0, score))
        result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.75) or 0.75)))
        highlights = [str(x) for x in (result.get("positives") or [])]
        result["highlights"] = [x for x in highlights if x][:20]
        return result

    def run_combined(
        self,
        draft: str,
        chapter_num: int,
        context: dict,
        language: str = "zh",
        native_style_profile: str = "",
        provider: str | None = None,
        model: str | None = None,
        inference: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        """Combined 6-dimension review in a single LLM call.

        Returns (struct_raw, factual_raw, progression_raw, aesthetic_raw, ai_flavor_raw, webnovel_raw) —
        same shape as separate run_*_structured() calls, fully drop-in.
        Never raises — always returns 6 dicts (empty defaults on any failure).
        """
        def _empty_defaults():
            return (
                ReviewScorecardSchema().model_dump(),
                FactualSubReviewSchema().model_dump(),
                ProgressionReviewSchema().model_dump(),
                ReviewScorecardSchema().model_dump(),
                AiFlavorSchema().model_dump(),
                WebnovelPrinciplesSchema().model_dump(),
            )

        template = "reviewer_combined"
        combined_inference = dict(inference or {})
        if "temperature" not in combined_inference:
            combined_inference["temperature"] = 0.18
        factual_ctx = _build_reviewer_context_json(context, reviewer_kind="factual")
        prog_ctx = _build_reviewer_context_json(context, reviewer_kind="progression")
        try:
            llm = get_llm_with_fallback(provider, model, inference=combined_inference)
            prompt = render_prompt(
                template,
                chapter_num=chapter_num,
                language=language,
                native_style_profile=(native_style_profile or "默认"),
                context_json=factual_ctx,
                progression_context_json=prog_ctx,
                draft=(draft[:7000]),
                role_section=build_reviewer_role_section("综合审校角色"),
                memory_policy_section=build_memory_governance_sections(include_constraint_usage=True),
            )
            result = _invoke_json_with_schema(
                llm,
                prompt,
                ReviewCombinedSchema,
                strict=False,
                stage="reviewer.combined",
                provider=provider,
                model=model,
                chapter_num=chapter_num,
                prompt_template=template,
                prompt_version="v1",
            )
        except Exception as exc:
            logger.warning("run_combined LLM call failed chapter=%s: %s", chapter_num, exc)
            return _empty_defaults()

        if not isinstance(result, dict) or result.get("raw") == "structured_output_fallback":
            logger.warning("run_combined structured fallback for chapter=%s", chapter_num)
            return _empty_defaults()

        def _clamp_score(d: dict) -> dict:
            s = float(d.get("score", 0.8))
            if s > 1:
                s = s / 100 if s <= 100 else 0.8
            d["score"] = max(0.0, min(1.0, s))
            c = float(d.get("confidence", 0.75))
            d["confidence"] = max(0.0, min(1.0, c))
            return d

        struct_raw = _clamp_score(dict(result.get("structure") or {}))
        factual_raw = _clamp_score(dict(result.get("factual") or {}))
        if not factual_raw.get("contradictions"):
            factual_raw["contradictions"] = [
                str(x.get("claim") or "") for x in (factual_raw.get("must_fix") or [])
                if isinstance(x, dict) and x.get("claim")
            ][:20]
        progression_raw = _clamp_score(dict(result.get("progression") or {}))
        aesthetic_raw = _clamp_score(dict(result.get("aesthetic") or {}))
        if not aesthetic_raw.get("highlights"):
            aesthetic_raw["highlights"] = aesthetic_raw.get("positives") or []
        ai_flavor_raw = dict(result.get("ai_flavor") or AiFlavorSchema().model_dump())
        webnovel_raw = dict(result.get("webnovel_principles") or WebnovelPrinciplesSchema().model_dump())

        return struct_raw, factual_raw, progression_raw, aesthetic_raw, ai_flavor_raw, webnovel_raw

    def run_cross_chapter_check(
        self,
        draft: str,
        chapter_num: int,
        recent_summaries: list,
        char_states: list,
        target_language: str,
        provider: str | None = None,
        model: str | None = None,
        inference: dict | None = None,
    ) -> dict:
        """Check cross-chapter consistency: draft vs recent summaries and character states."""
        template = "cross_chapter_check"
        llm = get_llm_with_fallback(provider, model, inference=inference)
        prompt = render_prompt(
            template,
            draft=draft,
            chapter_num=chapter_num,
            recent_summaries=recent_summaries,
            char_states=char_states,
            target_language=target_language,
        )
        try:
            result = _invoke_json_with_schema(
                llm,
                prompt,
                CrossChapterCheckSchema,
                strict=False,
                stage="reviewer.cross_chapter",
                provider=provider,
                model=model,
                chapter_num=chapter_num,
                prompt_template=template,
                prompt_version="v1",
            )
            return result if isinstance(result, dict) else {"contradictions": []}
        except Exception as exc:
            logger.warning("run_cross_chapter_check failed: %s", exc)
            return {"contradictions": []}

    def run_unknown_character_check(
        self,
        draft: str,
        chapter_num: int,
        unknown_names: list,
        roster: list,
        recent_summaries: list,
        target_language: str,
        provider: str | None = None,
        model: str | None = None,
        inference: dict | None = None,
    ) -> dict:
        """Classify unknown characters as unreasonable, new_reasonable, or minor."""
        template = "unknown_character_check"
        llm = get_llm_with_fallback(provider, model, inference=inference)
        prompt = render_prompt(
            template,
            draft=draft,
            chapter_num=chapter_num,
            unknown_names=unknown_names,
            roster=roster,
            recent_summaries=recent_summaries,
            target_language=target_language,
        )
        try:
            result = _invoke_json_with_schema(
                llm,
                prompt,
                UnknownCharacterCheckSchema,
                strict=False,
                stage="reviewer.unknown_character",
                provider=provider,
                model=model,
                chapter_num=chapter_num,
                prompt_template=template,
                prompt_version="v1",
            )
            return result if isinstance(result, dict) else {"verdicts": []}
        except Exception as exc:
            logger.warning("run_unknown_character_check failed: %s", exc)
            return {"verdicts": []}


class FinalizerAgent:
    """Polish and finalize content."""

    def run(
        self,
        draft: str,
        feedback: str,
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
        inference: dict[str, Any] | None = None,
        word_count: int | None = None,
        strategy_key: str | None = None,
        native_style_profile: str = "",
        pacing_mode: str | None = None,
        closure_state: dict[str, Any] | None = None,
    ) -> str:
        # If no significant feedback, return draft as-is
        if not feedback or feedback == "Auto-review: acceptable quality":
            return draft

        template = "finalizer_polish"
        prompt = render_prompt(
            template,
            feedback=feedback,
            draft=draft,
            language=language,
            role_section=build_finalizer_role_section(),
            memory_policy_section=build_memory_governance_sections(include_constraint_usage=True),
            style_overlay_section=build_style_overlay_sections(
                strategy_key=strategy_key,
                native_style_profile=native_style_profile,
                language=language,
                pacing_mode=pacing_mode,
                closure_state=closure_state,
            ),
            **build_chapter_length_prompt_kwargs(word_count),
        )

        try:
            content = invoke_chapter_body_structured(
                prompt=prompt,
                stage="finalizer",
                provider=provider,
                model=model,
                inference=inference,
                prompt_template=template,
                prompt_version="v2",
            )
            logger.info(f"FinalizerAgent completed, length: {len(content)}")
            return content
        except OutputContractError:
            logger.exception(
                "FinalizerAgent contract failed provider=%s model=%s language=%s",
                provider,
                model,
                language,
            )
            raise
        except Exception as e:
            logger.error(f"FinalizerAgent failed: {e}")
            root = e
            while getattr(root, "__cause__", None) is not None:
                root = root.__cause__
            root_type = type(root).__name__
            root_msg = str(root).strip().replace("\n", " ")
            if len(root_msg) > 240:
                root_msg = root_msg[:240]
            raise RuntimeError(
                f"finalizer failed provider={provider or '__default__'} model={model or '__default__'} "
                f"cause={root_type}: {root_msg or 'unknown error'}"
            ) from e


class FinalReviewerAgent:
    """Final quality check."""

    def run(self, content: str, language: str = "zh") -> bool:
        # Basic validation - ensure content is not empty and has reasonable length
        if not content or len(content) < 100:
            logger.warning("FinalReviewerAgent: content too short")
            return False
        logger.info("FinalReviewerAgent: content passed final review")
        return True

    def run_full_book(
        self,
        volume_reports: list[dict],
        recent_summaries: list[dict],
        unresolved_foreshadows: list[dict],
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
        inference: dict[str, Any] | None = None,
    ) -> dict:
        template = "final_book_review"
        llm = get_llm_with_fallback(provider, model, inference=inference)
        prompt = render_prompt(
            template,
            volume_reports=volume_reports,
            recent_summaries=recent_summaries,
            unresolved_foreshadows=unresolved_foreshadows,
            language=language,
            role_section=build_reviewer_role_section("整书终审角色"),
            contract_section=render_prompt("contract/final_book_review_contract"),
        )
        try:
            data = _invoke_json_with_schema(
                llm,
                prompt,
                FinalBookReviewSchema,
                strict=True,
                provider=provider,
                model=model,
                stage="reviewer.book",
                prompt_template=template,
                prompt_version="v2",
            )
            if not isinstance(data, dict):
                return _final_book_review_fallback("invalid_final_review_payload")
            return FinalBookReviewSchema.model_validate(data).model_dump()
        except Exception as e:
            logger.error(f"FinalReviewerAgent full-book review failed: {e}")
            return _final_book_review_fallback(str(e))


class FactExtractorAgent:
    """Extract structured facts/events from finalized chapter text."""

    def run(
        self,
        chapter_num: int,
        content: str,
        outline: dict | None = None,
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
        inference: dict[str, Any] | None = None,
    ) -> dict:
        template = "fact_extractor_structured"
        llm = get_llm_with_fallback(provider, model, inference=inference)
        prompt = render_prompt(
            template,
            chapter_num=chapter_num,
            language=language,
            outline_json=(json.dumps(outline or {}, ensure_ascii=False)[:1500]),
            content=(content[:7000]),
            role_section=build_fact_extractor_role_section(),
        )
        data = _invoke_json_with_schema(
            llm,
            prompt,
            FactExtractionSchema,
            strict=True,
            stage="fact_extractor",
            provider=provider,
            model=model,
            chapter_num=chapter_num,
            prompt_template=template,
            prompt_version="v2",
        )
        if not isinstance(data, dict):
            return {"events": [], "entities": [], "facts": []}
        return _trim_fact_lists(data)

    def run_foreshadow_extraction(
        self,
        content: str,
        chapter_num: int,
        outline: dict,
        target_language: str,
        provider: str | None = None,
        model: str | None = None,
        inference: dict[str, Any] | None = None,
    ) -> dict:
        from app.prompts import render_prompt as _render
        prompt = _render(
            "foreshadow_extraction",
            content=content[:4000],
            chapter_num=chapter_num,
            outline_foreshadowing=str(outline.get("foreshadowing") or "无"),
            outline_payoff=str(outline.get("payoff") or "无"),
            target_language=target_language,
        )
        try:
            llm = get_llm_with_fallback(provider, model, inference=inference)
            resp = llm.invoke(prompt)
            text = response_to_text(resp)
            text = _extract_json_block(text)
            result = json.loads(text)
            return result if isinstance(result, dict) else {"planted": [], "resolved": []}
        except Exception as exc:
            logger.warning("run_foreshadow_extraction failed chapter=%s error=%s", chapter_num, exc)
            return {"planted": [], "resolved": []}

    async def run_relation_extraction(
        self,
        chapter_text: str,
        existing_characters: list[str],
        provider: str | None = None,
        model: str | None = None,
        inference: dict[str, Any] | None = None,
    ) -> CharacterRelationsSchema:
        template = "character_relation_extraction"
        prompt = render_prompt(
            template,
            chapter_text=chapter_text[:5000],
            existing_characters=", ".join(existing_characters),
        )
        try:
            llm = get_llm_with_fallback(provider, model, inference=inference)
            resp = llm.invoke(prompt)
            text = response_to_text(resp)
            text = _extract_json_block(text)
            data = json.loads(text)
            return CharacterRelationsSchema.model_validate(data)
        except Exception as exc:
            logger.warning("run_relation_extraction failed error=%s", exc)
            return CharacterRelationsSchema()


class ProgressionMemoryAgent:
    """Extract structured progression and continuity memory from finalized chapter text."""

    def run(
        self,
        chapter_num: int,
        content: str,
        outline: dict | None = None,
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
        inference: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        template = "chapter_memory_structured"
        llm = get_llm_with_fallback(provider, model, inference=inference)
        prompt = render_prompt(
            template,
            chapter_num=chapter_num,
            language=language,
            outline_json=(json.dumps(outline or {}, ensure_ascii=False)[:2200]),
            content=(content[:7000]),
            role_section=build_progression_role_section(),
            memory_policy_section=build_memory_governance_sections(include_constraint_usage=True),
        )
        data = _invoke_json_with_schema(
            llm,
            prompt,
            ChapterMemoryExtractionSchema,
            strict=True,
            stage="progression_memory",
            provider=provider,
            model=model,
            chapter_num=chapter_num,
            prompt_template=template,
            prompt_version="v1",
        )
        if not isinstance(data, dict):
            return {
                "advancement": {},
                "transition": {},
                "advancement_confidence": 0.0,
                "transition_confidence": 0.0,
                "validation_notes": [],
            }
        trimmed = _trim_progression_memory_payload(data)
        advancement = trimmed.get("advancement") or {}
        transition = trimmed.get("transition") or {}
        return {
            "advancement": dict(advancement) if isinstance(advancement, dict) else {},
            "transition": dict(transition) if isinstance(transition, dict) else {},
            "advancement_confidence": float(trimmed.get("advancement_confidence") or 0.0),
            "transition_confidence": float(trimmed.get("transition_confidence") or 0.0),
            "validation_notes": [str(x)[:180] for x in (trimmed.get("validation_notes") or []) if str(x).strip()][:6],
        }
