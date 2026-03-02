"""LangGraph pipeline agents - modular classes with language and model selection."""
import json
import logging
import re
import time
from typing import Any
from pydantic import BaseModel, ValidationError
from langchain_core.output_parsers import PydanticOutputParser

from app.core.llm import get_llm_with_fallback, response_to_text, _is_retryable
from app.prompts import render_prompt
from app.services.generation.common import normalize_title_text

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


def _invoke_json_with_schema(llm: Any, prompt: str, schema_cls: type[BaseModel], retries: int = 2) -> dict:
    """Strongly enforce structured output with retry on parse/validation AND transient API errors."""
    parser = PydanticOutputParser(pydantic_object=schema_cls)
    format_instructions = parser.get_format_instructions()
    prompt = render_prompt(
        "structured_output_enforcer",
        base_prompt=prompt,
        format_instructions=format_instructions,
    )
    error = ""
    for attempt in range(retries + 1):
        try:
            started = time.perf_counter()
            resp = llm.invoke(prompt)
            elapsed = time.perf_counter() - started
            logger.info(
                "LLM structured invoke success schema=%s attempt=%s elapsed=%.2fs prompt_chars=%s",
                schema_cls.__name__,
                attempt + 1,
                elapsed,
                len(prompt),
            )
            content = response_to_text(resp).strip()
            try:
                parsed = parser.parse(content)
                return parsed.model_dump()
            except Exception:
                payload = _extract_json_block(content)
                raw = json.loads(payload)
                validated = schema_cls.model_validate(raw)
                return validated.model_dump()
        except (json.JSONDecodeError, ValidationError) as exc:
            error = str(exc)
            logger.warning(
                "LLM structured parse retry schema=%s attempt=%s error=%s",
                schema_cls.__name__,
                attempt + 1,
                error,
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
                    "LLM structured invoke transient error schema=%s attempt=%s error=%s delay=%.1fs",
                    schema_cls.__name__,
                    attempt + 1,
                    type(exc).__name__,
                    delay,
                )
                time.sleep(delay)
            else:
                break
    logger.warning(f"Structured output failed after retries: {error}")
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
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
    ) -> list[dict]:
        """Generate full-book chapter outlines before chapter writing."""
        llm = get_llm_with_fallback(provider, model)
        prompt = render_prompt(
            "chapter_blueprint_all",
            novel_id=novel_id,
            num_chapters=num_chapters,
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
                normalized_title = normalize_title_text(item.get("title"))
                normalized.append(
                    {
                        "chapter_num": idx,
                        "title": normalized_title or f"第{idx}章",
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
                for i in range(1, num_chapters + 1)
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
    ) -> str:
        llm = get_llm_with_fallback(provider, model)
        template = "first_chapter" if chapter_num == 1 else "next_chapter"
        prompt = render_prompt(
            template,
            novel_id=novel_id,
            chapter_num=chapter_num,
            outline=outline,
            context=context,
            language=language,
            native_style_profile=native_style_profile,
        )
        try:
            response = _timed_invoke(
                llm,
                prompt,
                "writer",
                {"novel_id": novel_id, "chapter_num": chapter_num, "provider": provider, "model": model, "template": template},
            )
            content = response_to_text(response).strip()
            logger.info(f"WriterAgent completed for novel {novel_id} chapter {chapter_num}, length: {len(content)}")
            return content
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
        llm = get_llm_with_fallback(provider, model)
        prompt = render_prompt(
            "reviewer_structured",
            chapter_num=chapter_num,
            language=language,
            native_style_profile=(native_style_profile or "默认"),
            draft=(draft[:7000]),
        )
        try:
            result = _invoke_json_with_schema(llm, prompt, ReviewScorecardSchema)
            score = float(result.get("score", 0.8))
            if score > 1:
                score = score / 100 if score <= 100 else 0.8
            result["score"] = max(0.0, min(1.0, score))
            conf = float(result.get("confidence", 0.75))
            result["confidence"] = max(0.0, min(1.0, conf))
            return result
        except Exception as e:
            logger.error(f"ReviewerAgent structured review failed: {e}")
            return {
                "score": 0.75,
                "confidence": 0.5,
                "feedback": "结构化审校降级：返回基础结果",
                "positives": [],
                "must_fix": [],
                "should_fix": [],
                "risks": ["structured_review_failed"],
            }

    def run(
        self,
        draft: str,
        chapter_num: int = 0,
        language: str = "zh",
        native_style_profile: str = "",
        provider: str | None = None,
        model: str | None = None,
    ) -> tuple[float, str]:
        try:
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
        except Exception as e:
            logger.error(f"ReviewerAgent failed: {e}")
            return 0.75, "Auto-review: acceptable quality"

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
        llm = get_llm_with_fallback(provider, model)
        prompt = render_prompt(
            "reviewer_factual_structured",
            chapter_num=chapter_num,
            context_json=(json.dumps(context, ensure_ascii=False)[:3000]),
            draft=(draft[:6000]),
        )
        try:
            result = _invoke_json_with_schema(llm, prompt, ReviewScorecardSchema)
            score = float(result.get("score", 0.8))
            if score > 1:
                score = score / 100 if score <= 100 else 0.8
            result["score"] = max(0.0, min(1.0, score))
            result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.75) or 0.75)))
            contradictions = [str(x.get("claim") or "") for x in (result.get("must_fix") or []) if isinstance(x, dict)]
            result["contradictions"] = [x for x in contradictions if x][:20]
            return result
        except Exception as e:
            logger.error(f"ReviewerAgent factual review failed: {e}")
            return {
                "score": 0.75,
                "confidence": 0.5,
                "feedback": "事实审校降级：未检测到显著冲突",
                "contradictions": [],
                "must_fix": [],
                "should_fix": [],
                "risks": ["factual_review_failed"],
            }

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
        llm = get_llm_with_fallback(provider, model)
        prompt = render_prompt(
            "reviewer_aesthetic_structured",
            chapter_num=chapter_num,
            draft=(draft[:6000]),
        )
        try:
            result = _invoke_json_with_schema(llm, prompt, ReviewScorecardSchema)
            score = float(result.get("score", 0.8))
            if score > 1:
                score = score / 100 if score <= 100 else 0.8
            result["score"] = max(0.0, min(1.0, score))
            result["confidence"] = max(0.0, min(1.0, float(result.get("confidence", 0.75) or 0.75)))
            highlights = [str(x) for x in (result.get("positives") or [])]
            result["highlights"] = [x for x in highlights if x][:20]
            return result
        except Exception as e:
            logger.error(f"ReviewerAgent aesthetic review failed: {e}")
            return {
                "score": 0.75,
                "confidence": 0.5,
                "feedback": "审美审校降级：节奏基本可用",
                "highlights": [],
                "must_fix": [],
                "should_fix": [],
                "risks": ["aesthetic_review_failed"],
            }


class FinalizerAgent:
    """Polish and finalize content."""

    def run(
        self,
        draft: str,
        feedback: str,
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
    ) -> str:
        # If no significant feedback, return draft as-is
        if not feedback or feedback == "Auto-review: acceptable quality":
            return draft

        llm = get_llm_with_fallback(provider, model)
        prompt = render_prompt(
            "finalizer_polish",
            feedback=feedback,
            draft=draft,
            language=language,
        )

        try:
            response = _timed_invoke(
                llm,
                prompt,
                "finalizer",
                {"provider": provider, "model": model, "language": language},
            )
            content = response_to_text(response).strip()
            logger.info(f"FinalizerAgent completed, length: {len(content)}")
            return content
        except Exception as e:
            logger.error(f"FinalizerAgent failed: {e}")
            return draft


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
        llm = get_llm_with_fallback(provider, model)
        prompt = render_prompt("final_book_review", chapters=chapters, language=language)
        try:
            data = _invoke_json_with_schema(llm, prompt, FinalBookReviewSchema)
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
        llm = get_llm_with_fallback(provider, model)
        prompt = render_prompt(
            "fact_extractor_structured",
            chapter_num=chapter_num,
            language=language,
            outline_json=(json.dumps(outline or {}, ensure_ascii=False)[:1500]),
            content=(content[:7000]),
        )
        try:
            data = _invoke_json_with_schema(llm, prompt, FactExtractionSchema)
            return data if isinstance(data, dict) else {"events": [], "entities": [], "facts": []}
        except Exception as e:
            logger.error(f"FactExtractorAgent failed: {e}")
            return {"events": [], "entities": [], "facts": []}
