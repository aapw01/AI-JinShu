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
from app.core.llm_contract import invoke_chapter_body_structured
from app.prompts import render_prompt
from app.services.generation.common import normalize_title_text
from app.services.generation.contracts import OutputContractError
from app.services.generation.length_control import build_chapter_length_prompt_kwargs
from app.services.memory.progression_state import normalize_outline_contract

logger = logging.getLogger(__name__)

_AGENT_MAX_RETRIES = 3
_AGENT_RETRY_BACKOFF = [2.0, 5.0, 10.0]


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


class FactExtractionSchema(BaseModel):
    events: list[dict[str, Any]] = []
    entities: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []


class ChapterAdvancementMemorySchema(BaseModel):
    chapter_objective: str = ""
    actual_progress: str = ""
    new_information: list[str] = []
    irreversible_change: str = ""
    relationship_delta: str = ""
    conflict_axis: str = ""
    payoff_kind: str = ""
    reveal_kind: str = ""
    resolved_threads: list[str] = []
    new_unresolved_threads: list[str] = []
    forbidden_repeats: list[str] = []
    phase: str = ""
    major_beats: list[str] = []


class ChapterTransitionMemorySchema(BaseModel):
    ending_scene: str = ""
    character_positions: list[str] = []
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
    ) -> dict:
        llm = get_llm_with_fallback(provider, model)
        result: dict[str, Any] = {}
        templates = [
            ("constitution", "constitution"),
            ("specification", "specify_story"),
            ("creative_plan", "plan_story"),
            ("tasks", "tasks_breakdown"),
        ]
        started_all = time.perf_counter()
        for key, tpl in templates:
            try:
                prompt = render_prompt(
                    tpl,
                    novel=novel,
                    num_chapters=num_chapters,
                    language=language,
                )
                resp = _timed_invoke(
                    llm,
                    prompt,
                    f"prewrite.{key}",
                    {"num_chapters": num_chapters, "provider": provider, "model": model},
                )
                parsed = _parse_json_response(response_to_text(resp))
                result[key] = parsed if isinstance(parsed, dict) else {"raw": str(parsed)}
            except Exception as e:
                logger.error(f"PrewritePlannerAgent {key} failed: {e}")
                result[key] = {"raw": f"fallback-{key}"}
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
        """Generate full-book chapter outlines before chapter writing."""
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
        word_count: int | None = None,
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
            **build_chapter_length_prompt_kwargs(word_count),
        )
        try:
            content = invoke_chapter_body_structured(
                prompt=prompt,
                stage="writer",
                chapter_num=chapter_num,
                provider=provider,
                model=model,
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
        word_count: int | None = None,
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
            **build_chapter_length_prompt_kwargs(word_count),
        )

        try:
            content = invoke_chapter_body_structured(
                prompt=prompt,
                stage="finalizer",
                provider=provider,
                model=model,
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
        chapters: list[dict],
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
        inference: dict[str, Any] | None = None,
    ) -> dict:
        template = "final_book_review"
        llm = get_llm_with_fallback(provider, model, inference=inference)
        prompt = render_prompt(template, chapters=chapters, language=language)
        try:
            data = _invoke_json_with_schema(
                llm,
                prompt,
                FinalBookReviewSchema,
                stage="reviewer.book",
                prompt_template=template,
                prompt_version="v2",
            )
            return data if isinstance(data, dict) else {"score": 0.8, "feedback": "ok"}
        except Exception as e:
            logger.error(f"FinalReviewerAgent full-book review failed: {e}")
            return {"score": 0.8, "feedback": "final review fallback"}


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
        return data if isinstance(data, dict) else {"events": [], "entities": [], "facts": []}

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
        advancement = data.get("advancement") or {}
        transition = data.get("transition") or {}
        return {
            "advancement": dict(advancement) if isinstance(advancement, dict) else {},
            "transition": dict(transition) if isinstance(transition, dict) else {},
            "advancement_confidence": float(data.get("advancement_confidence") or 0.0),
            "transition_confidence": float(data.get("transition_confidence") or 0.0),
            "validation_notes": [str(x)[:180] for x in (data.get("validation_notes") or []) if str(x).strip()][:8],
        }
