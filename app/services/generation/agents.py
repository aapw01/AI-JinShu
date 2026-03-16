"""LangGraph pipeline agents - modular classes with language and model selection."""
import json
import logging
import re
import time
import hashlib
from typing import Any
from pydantic import BaseModel, ValidationError
from langchain_core.output_parsers import PydanticOutputParser

from app.core.llm import get_llm_with_fallback, response_to_text, _is_retryable
from app.core.llm_contract import invoke_chapter_body_structured
from app.prompts import render_prompt
from app.services.generation.common import normalize_title_text
from app.services.generation.contracts import OutputContractError

logger = logging.getLogger(__name__)

_AGENT_MAX_RETRIES = 3
_AGENT_RETRY_BACKOFF = [2.0, 5.0, 10.0]


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
    summary: str | None = None


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
            result = _parse_json_response(response.content)
            title = normalize_title_text(result.get("title"))
            if title:
                result["title"] = title
            else:
                result["title"] = f"Chapter {chapter_num}"
            logger.info(f"OutlinerAgent completed for novel {novel_id} chapter {chapter_num}")
            return result
        except Exception as e:
            logger.error(f"OutlinerAgent failed: {e}")
            return {"title": f"Chapter {chapter_num}", "outline": "auto-generated outline"}

    def run_full_book(
        self,
        novel_id: str,
        num_chapters: int,
        prewrite: dict,
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
                normalized_title = normalize_title_text(item.get("title"))
                normalized.append(
                    {
                        "chapter_num": chapter_no,
                        "title": normalized_title or f"第{chapter_no}章",
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
                    }
                )
            return normalized
        except Exception as e:
            logger.error(f"OutlinerAgent run_full_book failed: {e}")
            return [
                {
                    "chapter_num": i,
                    "title": f"第{i}章",
                    "outline": "auto-generated outline",
                    "role": "推进剧情",
                    "purpose": "推进主线",
                    "suspense_level": "中",
                    "foreshadowing": "",
                    "plot_twist_level": "低",
                    "hook": "",
                    "payoff": "",
                    "mini_climax": "none",
                    "summary": "",
                }
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
            word_count=word_count,
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
    ) -> dict[str, Any]:
        template = "reviewer_structured"
        llm = get_llm_with_fallback(provider, model)
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
    ) -> tuple[float, str]:
        result = self.run_structured(
            draft=draft,
            chapter_num=chapter_num,
            language=language,
            native_style_profile=native_style_profile,
            provider=provider,
            model=model,
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
    ) -> tuple[float, str, list[str]]:
        result = self.run_factual_structured(draft, chapter_num, context, language, provider, model)
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
    ) -> dict[str, Any]:
        template = "reviewer_factual_structured"
        llm = get_llm_with_fallback(provider, model)
        prompt = render_prompt(
            template,
            chapter_num=chapter_num,
            context_json=(json.dumps(context, ensure_ascii=False)[:3000]),
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

    def run_aesthetic(
        self,
        draft: str,
        chapter_num: int,
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
    ) -> tuple[float, str, list[str]]:
        result = self.run_aesthetic_structured(draft, chapter_num, language, provider, model)
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
    ) -> dict[str, Any]:
        template = "reviewer_aesthetic_structured"
        llm = get_llm_with_fallback(provider, model)
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
            word_count=word_count,
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
    ) -> dict:
        template = "final_book_review"
        llm = get_llm_with_fallback(provider, model)
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
    ) -> dict:
        template = "fact_extractor_structured"
        llm = get_llm_with_fallback(provider, model)
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
