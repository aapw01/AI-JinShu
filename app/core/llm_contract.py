"""章节正文结构化输出契约层。

模块职责：
- 强制模型按 `ChapterBodySchema` 产出结构化结果，而不是裸文本。
- 统一处理 method 选择、schema 重试、错误归一化、prompt 元数据追踪。
- 为后续排查提供 prompt template / version / hash 级别的观测信息。

系统位置：
- 上游是 prompt 模板渲染与章节写作节点。
- 下游是 `validate_chapter_body()` 等正文质量校验逻辑。

面试可讲点：
- 为什么“让模型严格输出 JSON”还不够，必须再做 schema 校验和重试。
- 为什么 prompt hash/模板版本要和失败日志绑定。
- 为什么这里的失败类型要区分 parse/schema/provider call 三层。
"""
from __future__ import annotations

import hashlib
import logging
from contextvars import ContextVar
from typing import Any

from pydantic import ValidationError

from app.core.constants import LLM_OUTPUT_MAX_SCHEMA_RETRIES, LLM_OUTPUT_MIN_CHARS
from app.core.llm import get_llm, resolve_effective_adapter
from app.services.generation.contracts import ChapterBodySchema, OutputContractError, validate_chapter_body

logger = logging.getLogger(__name__)
_LAST_PROMPT_META: ContextVar[dict[str, Any] | None] = ContextVar("llm_contract_last_prompt_meta", default=None)


def _method_order(adapter_type: str) -> list[str]:
    """按 provider 兼容性决定结构化输出方法尝试顺序。"""
    if adapter_type == "anthropic":
        return ["function_calling", "json_schema"]
    if adapter_type == "gemini":
        return ["json_schema", "function_calling", "json_mode"]
    return ["json_schema", "function_calling", "json_mode"]


def _extract_parsed_body(parsed: Any) -> str:
    """执行 extract parsed body 相关辅助逻辑。"""
    if parsed is None:
        return ""
    if isinstance(parsed, ChapterBodySchema):
        return str(parsed.chapter_body or "")
    if hasattr(parsed, "model_dump"):
        data = parsed.model_dump()
        if isinstance(data, dict):
            return str(data.get("chapter_body") or "")
    if isinstance(parsed, dict):
        return str(parsed.get("chapter_body") or "")
    return ""


def _provider_candidates(
    provider: str | None,
    model: str | None,
    _unused: int,
) -> list[tuple[str | None, str | None]]:
    """返回本次调用允许尝试的 provider / model 候选。

    当前项目已收敛为单主模型，因此这里只保留一个候选位，接口形状
    则为未来扩展 provider fallback 预留。
    """
    if provider is not None:
        return [(provider, model)]
    return [(None, model)]


def prompt_hash(prompt: str) -> str:
    """计算 prompt 的短 hash，供日志和问题回放定位使用。"""
    return hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()[:16]


def get_last_prompt_meta() -> dict[str, Any] | None:
    """返回最后一个提示词元数据。"""
    value = _LAST_PROMPT_META.get()
    if not value:
        return None
    return dict(value)


def invoke_chapter_body_structured(
    *,
    prompt: str,
    stage: str,
    provider: str | None = None,
    model: str | None = None,
    inference: dict[str, Any] | None = None,
    chapter_num: int | None = None,
    retries: int | None = None,
    min_chars: int | None = None,
    max_provider_fallbacks: int | None = None,
    prompt_template: str | None = None,
    prompt_version: str = "v2",
) -> str:
    """以严格结构化契约调用 LLM 生成章节正文。

    这层的职责不是“帮模型写文”，而是把模型输出约束到工程系统能消费的形态：
    - 先选 provider 支持的方法顺序。
    - 再按 schema 失败重试若干次。
    - 最后把失败压缩成统一的 `OutputContractError`。

    面试里可以把它概括为：
    “Prompt 工程负责提高成功率，contract 层负责保证可消费性。”
    """

    schema_retries = max(0, int(retries if retries is not None else LLM_OUTPUT_MAX_SCHEMA_RETRIES))
    min_body_chars = int(min_chars if min_chars is not None else LLM_OUTPUT_MIN_CHARS)
    p_template = str(prompt_template or stage)
    p_version = str(prompt_version or "v2")
    p_hash = prompt_hash(prompt)

    failures: list[str] = []
    candidates = _provider_candidates(provider, model, 0)

    for candidate_provider, candidate_model in candidates:
        adapter, _ = resolve_effective_adapter(candidate_provider)
        methods = _method_order(adapter)
        for method in methods:
            for attempt in range(schema_retries + 1):
                try:
                    llm = get_llm(candidate_provider, candidate_model, inference=inference)
                    kwargs: dict[str, Any] = {
                        "method": method,
                        "include_raw": True,
                    }
                    # strict=True only applies to json_schema / function_calling;
                    # json_mode does not support it.
                    if adapter == "openai_compatible" and method != "json_mode":
                        kwargs["strict"] = True
                    try:
                        structured = llm.with_structured_output(ChapterBodySchema, **kwargs)
                    except (TypeError, ValueError):
                        # Safety net for unexpected strict-incompatible combinations
                        kwargs.pop("strict", None)
                        structured = llm.with_structured_output(ChapterBodySchema, **kwargs)
                    result = structured.invoke(prompt)
                    raw_resp: Any = None
                    parsed: Any = None
                    parsing_error: Any = None
                    if isinstance(result, dict) and any(k in result for k in ("raw", "parsed", "parsing_error")):
                        raw_resp = result.get("raw")
                        parsed = result.get("parsed")
                        parsing_error = result.get("parsing_error")
                    else:
                        parsed = result
                    if parsing_error is not None:
                        raise OutputContractError(
                            code="MODEL_OUTPUT_PARSE_FAILED",
                            stage=stage,
                            chapter_num=chapter_num,
                            provider=candidate_provider,
                            model=candidate_model,
                            detail=str(parsing_error),
                            retryable=True,
                        )
                    chapter_body = _extract_parsed_body(parsed)
                    if not chapter_body and raw_resp is not None:
                        chapter_body = _extract_parsed_body(raw_resp)
                    chapter_body = validate_chapter_body(
                        chapter_body,
                        stage=stage,
                        chapter_num=chapter_num,
                        provider=candidate_provider,
                        model=candidate_model,
                        min_chars=min_body_chars,
                    )
                    logger.info(
                        "llm.output.contract.success stage=%s template=%s version=%s prompt_hash=%s provider=%s model=%s method=%s attempt=%s",
                        stage,
                        p_template,
                        p_version,
                        p_hash,
                        candidate_provider,
                        candidate_model,
                        method,
                        attempt + 1,
                    )
                    _LAST_PROMPT_META.set(
                        {
                            "stage": stage,
                            "prompt_template": p_template,
                            "prompt_version": p_version,
                            "prompt_hash": p_hash,
                            "provider": candidate_provider,
                            "model": candidate_model,
                        }
                    )
                    return chapter_body
                except ValidationError as exc:
                    err = OutputContractError(
                        code="MODEL_OUTPUT_SCHEMA_INVALID",
                        stage=stage,
                        chapter_num=chapter_num,
                        provider=candidate_provider,
                        model=candidate_model,
                        detail=str(exc),
                        retryable=True,
                    )
                    failures.append(str(err))
                except OutputContractError as exc:
                    failures.append(str(exc))
                except Exception as exc:
                    failures.append(
                        f"stage={stage} provider={candidate_provider} model={candidate_model} method={method} call_error={type(exc).__name__}: {exc}"
                    )
                logger.warning(
                    "llm.output.contract.retry stage=%s template=%s version=%s prompt_hash=%s provider=%s model=%s method=%s attempt=%s",
                    stage,
                    p_template,
                    p_version,
                    p_hash,
                    candidate_provider,
                    candidate_model,
                    method,
                    attempt + 1,
                )

                if attempt >= schema_retries:
                    break

    detail = " | ".join(failures[-6:]) if failures else "no usable output"
    logger.error(
        "llm.output.contract.exhausted stage=%s template=%s version=%s prompt_hash=%s provider=%s model=%s detail=%s",
        stage,
        p_template,
        p_version,
        p_hash,
        provider,
        model,
        detail,
    )
    _LAST_PROMPT_META.set(
        {
            "stage": stage,
            "prompt_template": p_template,
            "prompt_version": p_version,
            "prompt_hash": p_hash,
            "provider": provider,
            "model": model,
            "error": detail,
        }
    )
    raise OutputContractError(
        code="MODEL_OUTPUT_CONTRACT_EXHAUSTED",
        stage=stage,
        chapter_num=chapter_num,
        provider=provider,
        model=model,
        detail=detail,
        retryable=True,
    )
