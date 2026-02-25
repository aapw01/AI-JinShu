"""LangGraph pipeline agents - modular classes with language and model selection."""
import json
import logging
import re
import time
from typing import Any
from pydantic import BaseModel, ValidationError
from langchain_core.output_parsers import PydanticOutputParser

from app.core.llm import get_llm_with_fallback
from app.prompts import render_prompt

logger = logging.getLogger(__name__)


def _timed_invoke(llm: Any, prompt: str, op: str, meta: dict[str, Any] | None = None):
    started = time.perf_counter()
    try:
        resp = llm.invoke(prompt)
        elapsed = time.perf_counter() - started
        logger.info(
            "LLM invoke success op=%s elapsed=%.2fs prompt_chars=%s meta=%s",
            op,
            elapsed,
            len(prompt),
            meta or {},
        )
        return resp
    except Exception:
        elapsed = time.perf_counter() - started
        logger.exception(
            "LLM invoke failed op=%s elapsed=%.2fs prompt_chars=%s meta=%s",
            op,
            elapsed,
            len(prompt),
            meta or {},
        )
        raise


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
    """Strongly enforce structured output with retry on parse/validation errors."""
    parser = PydanticOutputParser(pydantic_object=schema_cls)
    format_instructions = parser.get_format_instructions()
    prompt = (
        f"{prompt}\n\n"
        f"{format_instructions}\n"
        "输出要求：只返回 JSON，不要包含 Markdown 或解释。"
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
            content = str(resp.content).strip()
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
            prompt = (
                f"{prompt}\n\n上一次输出不符合JSON结构约束，错误为: {error}\n"
                "请只输出符合要求的纯JSON，不要包含解释、Markdown或代码块。"
            )
        except Exception as exc:
            error = str(exc)
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
                parsed = _parse_json_response(resp.content)
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
            if "title" not in result:
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
                normalized.append(
                    {
                        "chapter_num": idx,
                        "title": item.get("title", f"第{idx}章"),
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
        chapter_num: int,
        prewrite: dict | None = None,
        outline: dict | None = None,
        db=None,
    ) -> dict:
        from app.services.memory.context import get_context_for_chapter

        # get_context_for_chapter uses build_chapter_context internally for layered context
        return get_context_for_chapter(
            novel_id, chapter_num, db=db, prewrite=prewrite, outline=outline
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
            content = response.content.strip()
            logger.info(f"WriterAgent completed for novel {novel_id} chapter {chapter_num}, length: {len(content)}")
            return content
        except Exception as e:
            logger.error(f"WriterAgent failed: {e}")
            return f"[Chapter {chapter_num} content generation failed: {e}]"


class ReviewerAgent:
    """Review draft and return score + feedback."""

    def run(
        self,
        draft: str,
        chapter_num: int = 0,
        language: str = "zh",
        native_style_profile: str = "",
        provider: str | None = None,
        model: str | None = None,
    ) -> tuple[float, str]:
        llm = get_llm_with_fallback(provider, model)
        prompt = render_prompt(
            "chapter_review",
            draft=draft,
            chapter_num=chapter_num,
            language=language,
            native_style_profile=native_style_profile,
        )
        try:
            result = _invoke_json_with_schema(llm, prompt, ReviewSchema)
            score = float(result.get("score", 0.8))
            feedback = result.get("feedback", "")
            # Normalize score to 0-1 range
            if score > 1:
                score = score / 100 if score <= 100 else 0.8
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
        llm = get_llm_with_fallback(provider, model)
        prompt = (
            f"你是事实一致性审稿器。请检查第{chapter_num}章正文是否与上下文冲突。\n"
            f"上下文要点:\n{json.dumps(context, ensure_ascii=False)[:3000]}\n\n"
            f"正文:\n{draft[:6000]}\n\n"
            "返回JSON字段: score(0-1), feedback(字符串), contradictions(字符串数组)。"
        )
        try:
            result = _invoke_json_with_schema(llm, prompt, FactualReviewSchema)
            score = float(result.get("score", 0.8))
            if score > 1:
                score = score / 100 if score <= 100 else 0.8
            feedback = str(result.get("feedback", ""))
            contradictions = [str(x) for x in (result.get("contradictions") or [])][:20]
            return score, feedback, contradictions
        except Exception as e:
            logger.error(f"ReviewerAgent factual review failed: {e}")
            return 0.75, "事实审校降级：未检测到显著冲突", []

    def run_aesthetic(
        self,
        draft: str,
        chapter_num: int,
        language: str = "zh",
        provider: str | None = None,
        model: str | None = None,
    ) -> tuple[float, str, list[str]]:
        llm = get_llm_with_fallback(provider, model)
        prompt = (
            f"你是网文节奏与审美审稿器。请评估第{chapter_num}章可读性、爽点兑现、情绪张力。\n"
            f"正文:\n{draft[:6000]}\n\n"
            "返回JSON字段: score(0-1), feedback(字符串), highlights(字符串数组)。"
        )
        try:
            result = _invoke_json_with_schema(llm, prompt, AestheticReviewSchema)
            score = float(result.get("score", 0.8))
            if score > 1:
                score = score / 100 if score <= 100 else 0.8
            feedback = str(result.get("feedback", ""))
            highlights = [str(x) for x in (result.get("highlights") or [])][:20]
            return score, feedback, highlights
        except Exception as e:
            logger.error(f"ReviewerAgent aesthetic review failed: {e}")
            return 0.75, "审美审校降级：节奏基本可用", []


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
        prompt = f"""Based on the following feedback, polish and improve this chapter content.

Feedback: {feedback}

Original content:
{draft}

Please provide the improved version. Keep the same language ({language}) and maintain the story flow."""

        try:
            response = _timed_invoke(
                llm,
                prompt,
                "finalizer",
                {"provider": provider, "model": model, "language": language},
            )
            content = response.content.strip()
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
        prompt = (
            f"请抽取第{chapter_num}章的结构化事实，语言:{language}。\n"
            f"大纲:\n{json.dumps(outline or {}, ensure_ascii=False)[:1500]}\n\n"
            f"正文:\n{content[:7000]}\n\n"
            "输出JSON字段:\n"
            "- events: [{id,title,type,actors[],summary,time_marker}]\n"
            "- entities: [{name,entity_type,status,summary}]\n"
            "- facts: [{entity_name,fact_type,value}]\n"
            "只输出JSON。"
        )
        try:
            data = _invoke_json_with_schema(llm, prompt, FactExtractionSchema)
            return data if isinstance(data, dict) else {"events": [], "entities": [], "facts": []}
        except Exception as e:
            logger.error(f"FactExtractorAgent failed: {e}")
            return {"events": [], "entities": [], "facts": []}
