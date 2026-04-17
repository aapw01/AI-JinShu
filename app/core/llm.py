"""统一的 LLM / Embedding 适配层。

模块职责：
- 把 OpenAI-compatible、Gemini、Anthropic 收敛成一套调用入口。
- 负责 provider 解析、推理参数归一化、重试、用量统计。
- 对上层暴露 `get_llm()` / `get_embedding_model()`，屏蔽底层 SDK 差异。

系统位置：
- 上游是系统设置运行时 `app.services.system_settings.runtime`。
- 下游是 Prompt 渲染、结构化输出、记忆检索、生成节点。

面试可讲点：
- 为什么项目没有把 provider 差异散落到业务节点里。
- 为什么“主模型连接”和“embedding 连接”要分开配置但允许复用。
- 为什么要在 SDK 外再包一层 proxy 做 token 统计与统一重试。
"""

from __future__ import annotations

from copy import deepcopy
import logging
import time
from typing import Any

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai._common import HarmBlockThreshold, HarmCategory
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.core.llm_usage import record_embedding_call, record_usage_from_response
from app.core.logging_config import log_event
from app.services.system_settings.runtime import get_embedding_runtime, get_primary_chat_runtime

logger = logging.getLogger(__name__)

_LLM_REQUEST_TIMEOUT = 120
_VALID_ADAPTER_TYPES = frozenset({"openai_compatible", "gemini", "anthropic"})
_GEMINI_HARM_CATEGORY_MAP = {item.value: item for item in HarmCategory}
_GEMINI_HARM_THRESHOLD_MAP = {item.value: item for item in HarmBlockThreshold}


def _coerce_part_text(part: Any) -> str:
    """把不同 SDK 的消息片段统一压平成纯文本。

    LangChain / OpenAI / Gemini 返回的 content 结构并不一致：
    有的直接是字符串，有的是分块列表，有的嵌套在 dict / 对象属性里。
    上层只关心“模型到底说了什么”，所以这里先把形态差异吃掉。
    """
    if part is None:
        return ""
    if isinstance(part, str):
        return part
    if isinstance(part, dict):
        text = part.get("text")
        if isinstance(text, str):
            return text
        content = part.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(x for x in (_coerce_part_text(i) for i in content) if x)
        return ""
    text_attr = getattr(part, "text", None)
    if isinstance(text_attr, str):
        return text_attr
    content_attr = getattr(part, "content", None)
    if isinstance(content_attr, str):
        return content_attr
    if isinstance(content_attr, list):
        return "\n".join(x for x in (_coerce_part_text(i) for i in content_attr) if x)
    return str(part)


def response_to_text(resp: Any) -> str:
    """Normalize LangChain/OpenAI response content to plain text."""
    content = getattr(resp, "content", resp)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p for p in (_coerce_part_text(item) for item in content) if p]
        return "\n".join(parts)
    return str(content or "")


def _resolve_api_key(_provider: str | None = None) -> str:
    """读取当前主模型运行时的 API Key。"""
    return str(get_primary_chat_runtime().get("api_key") or "")


def _resolve_base_url(_provider: str | None = None) -> str | None:
    """读取当前主模型运行时的 base_url，并去掉尾部斜杠。"""
    base_url = str(get_primary_chat_runtime().get("base_url") or "").strip()
    return base_url.rstrip("/") or None


def resolve_effective_adapter(provider_key: str | None) -> tuple[str, str]:
    """返回本次调用最终采用的协议适配器及其命中来源。"""
    primary = get_primary_chat_runtime()
    provider = str(provider_key or primary.get("provider") or "openai").strip().lower() or "openai"
    protocol_override = str(primary.get("protocol_override") or "").strip().lower() or None
    base_url = str(primary.get("base_url") or "").strip() or None
    if protocol_override in _VALID_ADAPTER_TYPES:
        return protocol_override, "override"
    if base_url:
        return "openai_compatible", "auto_infer"
    if provider == "anthropic":
        return "anthropic", "auto_native"
    if provider == "gemini":
        return "gemini", "auto_native"
    return "openai_compatible", "auto_native"


def _resolve_chat_provider_and_model(provider: str | None, model: str | None) -> tuple[str, str]:
    """返回当前调用真正生效的 provider 和 model。"""
    primary = get_primary_chat_runtime()
    resolved_provider = str(primary.get("provider") or "openai").strip().lower() or "openai"
    resolved_model = str(model or primary.get("model") or "gpt-4o-mini").strip() or "gpt-4o-mini"
    requested_provider = str(provider or "").strip().lower()
    if requested_provider and requested_provider != resolved_provider:
        log_event(
            logger,
            "llm.provider_override.ignored",
            level=logging.DEBUG,
            requested_provider=requested_provider,
            effective_provider=resolved_provider,
        )
    return resolved_provider, resolved_model


def _normalize_temperature(value: Any) -> float | None:
    """把温度参数转换为 provider SDK 能稳定接收的浮点值。"""
    if value in (None, ""):
        return None
    return float(value)


def _normalize_gemini_safety_settings(
    value: Any,
) -> dict[HarmCategory, HarmBlockThreshold] | None:
    """把配置层的 Gemini safety settings 规范化为 SDK 枚举。

    配置文件里用字符串最容易维护，但 SDK 需要的是枚举类型。
    这一步把“人类可配”转换成“SDK 可执行”。
    """
    if value in (None, "", []):
        return None
    if isinstance(value, dict):
        entries = [{"category": key, "threshold": item} for key, item in value.items()]
    elif isinstance(value, list):
        entries = value
    else:
        raise ValueError("gemini.safety_settings must be a list or mapping")

    normalized: dict[HarmCategory, HarmBlockThreshold] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("gemini.safety_settings items must be objects")
        category_raw = str(entry.get("category") or "").strip().upper()
        threshold_raw = str(entry.get("threshold") or "").strip().upper()
        category = _GEMINI_HARM_CATEGORY_MAP.get(category_raw)
        threshold = _GEMINI_HARM_THRESHOLD_MAP.get(threshold_raw)
        if category is None:
            raise ValueError(f"unsupported Gemini harm category: {category_raw or entry.get('category')!r}")
        if threshold is None:
            raise ValueError(f"unsupported Gemini harm threshold: {threshold_raw or entry.get('threshold')!r}")
        normalized[category] = threshold
    return normalized or None


def normalize_inference_for_provider(provider_key: str | None, inference: dict[str, Any] | None) -> dict[str, Any]:
    """把通用 inference 参数裁剪成当前 provider 真正支持的子集。

    例如 Gemini 的 `safety_settings` 只应在 Gemini adapter 下生效；
    如果把所有 provider 特有参数都无差别透传，最容易出现运行时兼容性问题。
    """
    raw = deepcopy(inference or {})
    if not raw:
        return {}

    adapter_type, _ = resolve_effective_adapter(provider_key)
    normalized: dict[str, Any] = {}
    temperature = _normalize_temperature(raw.get("temperature"))
    if temperature is not None:
        normalized["temperature"] = temperature

    gemini_cfg = raw.get("gemini")
    if gemini_cfg not in (None, {}):
        if adapter_type != "gemini":
            log_event(
                logger,
                "llm.inference.gemini_ignored",
                level=logging.DEBUG,
                provider=provider_key,
                effective_adapter=adapter_type,
            )
            return normalized
        if not isinstance(gemini_cfg, dict):
            raise ValueError("gemini inference settings must be an object")
        safety_settings = _normalize_gemini_safety_settings(gemini_cfg.get("safety_settings"))
        if safety_settings:
            normalized["safety_settings"] = safety_settings
    return normalized


def extract_provider_block(resp: Any) -> dict[str, str] | None:
    """从 provider 响应里提取“被安全策略拦截”的结构化信息。"""
    def _extract_meta_dict(payload: Any) -> dict[str, Any] | None:
        """从 dict 或 SDK 响应对象中提取最可能携带 provider 元数据的字典。"""
        if isinstance(payload, dict):
            return payload
        for attr in ("response_metadata", "additional_kwargs"):
            value = getattr(payload, attr, None)
            if isinstance(value, dict):
                return value
        return None

    meta = _extract_meta_dict(resp) or {}
    prompt_feedback = meta.get("promptFeedback") or meta.get("prompt_feedback")
    if not isinstance(prompt_feedback, dict):
        prompt_feedback = meta
    reason = str(
        prompt_feedback.get("blockReason")
        or prompt_feedback.get("block_reason")
        or prompt_feedback.get("finish_reason")
        or ""
    ).strip()
    if not reason:
        return None
    message = str(
        prompt_feedback.get("blockReasonMessage")
        or prompt_feedback.get("block_reason_message")
        or prompt_feedback.get("message")
        or ""
    ).strip()
    if not message and str(reason).lower() not in {"safety", "content_filter"} and not str(reason).upper().startswith("PROHIBITED_"):
        return None
    return {
        "reason": reason,
        "message": message,
    }


def _build_chat_model(provider_key: str, model_name: str, *, inference: dict[str, Any] | None = None) -> BaseChatModel:
    """按最终 adapter 构造底层 LangChain chat model。

    这里是 provider 差异的唯一汇聚点。上层生成节点不需要知道
    `ChatOpenAI`、`ChatAnthropic`、`ChatGoogleGenerativeAI` 的细节。
    """
    adapter_type, adapter_source = resolve_effective_adapter(provider_key)
    primary = get_primary_chat_runtime()
    resolved_base_url = _resolve_base_url(provider_key)
    resolved_api_key = _resolve_api_key(provider_key)
    normalized_inference = normalize_inference_for_provider(provider_key, inference)

    log_event(
        logger,
        "llm.adapter.resolved",
        provider=provider_key,
        effective_adapter=adapter_type,
        adapter_source=adapter_source,
        configured_provider=primary.get("provider"),
        base_url=(resolved_base_url or "")[:60],
    )

    if adapter_type == "anthropic":
        base_url = resolved_base_url or "https://api.anthropic.com"
        if base_url.endswith("/v1"):
            base_url = base_url[:-3].rstrip("/")
        return ChatAnthropic(
            base_url=base_url,
            model=model_name,
            api_key=resolved_api_key,
            timeout=_LLM_REQUEST_TIMEOUT,
            max_retries=0,
            **normalized_inference,
        )
    if adapter_type == "gemini":
        kwargs: dict[str, Any] = {
            "model": model_name,
            "google_api_key": resolved_api_key,
            "timeout": _LLM_REQUEST_TIMEOUT,
        }
        if resolved_base_url:
            kwargs["base_url"] = resolved_base_url
        kwargs.update(normalized_inference)
        return ChatGoogleGenerativeAI(**kwargs)
    return ChatOpenAI(
        base_url=resolved_base_url or "https://api.openai.com/v1",
        model=model_name,
        api_key=resolved_api_key,
        request_timeout=_LLM_REQUEST_TIMEOUT,
        max_retries=0,
        **normalized_inference,
    )


_RETRYABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    OSError,
)

_MAX_RETRIES = 3
_RETRY_BACKOFF = [1.0, 2.0, 4.0]


def _is_retryable(exc: Exception) -> bool:
    """判断一次模型调用失败是否值得自动重试。

    只对明显的连接类、限流类、服务不可用类错误做退避重试，
    避免把确定性业务错误也重放三遍。
    """
    if isinstance(exc, _RETRYABLE_EXCEPTIONS):
        return True
    name = type(exc).__name__
    if any(kw in name for kw in ("Timeout", "Connection", "RateLimit", "ServiceUnavailable", "APIConnectionError")):
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if isinstance(status, int) and status in {429, 500, 502, 503, 504}:
        return True
    return False


class _TrackedLLMProxy:
    """Thin proxy that records provider usage for every invoke call with retry."""

    def __init__(self, inner: Any, *, stage_prefix: str):
        """保存真实 LLM 对象，并为后续用量统计准备 stage 前缀。"""
        self._inner = inner
        self._stage_prefix = stage_prefix

    def invoke(self, *args: Any, **kwargs: Any):
        """同步调用模型，并统一做 token 统计与有限次重试。"""
        if not hasattr(self._inner, "invoke"):
            return self._inner
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = self._inner.invoke(*args, **kwargs)
                record_usage_from_response(resp, stage=self._stage_prefix)
                return resp
            except Exception as exc:
                last_exc = exc
                if not _is_retryable(exc) or attempt >= _MAX_RETRIES - 1:
                    raise
                delay = _RETRY_BACKOFF[attempt] if attempt < len(_RETRY_BACKOFF) else 4.0
                log_event(
                    logger,
                    "llm.invoke.retry",
                    level=logging.WARNING,
                    attempt=attempt + 1,
                    error_class=type(exc).__name__,
                    delay=delay,
                )
                time.sleep(delay)
        raise last_exc  # type: ignore[misc]

    async def ainvoke(self, *args: Any, **kwargs: Any):
        """异步调用模型，并统一做 token 统计与有限次重试。"""
        import asyncio

        if not hasattr(self._inner, "ainvoke"):
            return self._inner
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self._inner.ainvoke(*args, **kwargs)
                record_usage_from_response(resp, stage=self._stage_prefix)
                return resp
            except Exception as exc:
                last_exc = exc
                if not _is_retryable(exc) or attempt >= _MAX_RETRIES - 1:
                    raise
                delay = _RETRY_BACKOFF[attempt] if attempt < len(_RETRY_BACKOFF) else 4.0
                log_event(
                    logger,
                    "llm.ainvoke.retry",
                    level=logging.WARNING,
                    attempt=attempt + 1,
                    error_class=type(exc).__name__,
                    delay=delay,
                )
                await asyncio.sleep(delay)
        raise last_exc  # type: ignore[misc]

    def __getattr__(self, item: str):
        """把未知属性访问委托给被包装对象。"""
        return getattr(self._inner, item)

    def __getitem__(self, item: Any):
        """把下标访问转发给被包装对象。"""
        return self._inner[item]

    def __iter__(self):
        """暴露被包装对象的迭代接口。"""
        return iter(self._inner)

    def __len__(self) -> int:
        """返回被包装对象的长度。"""
        return len(self._inner)

    def __contains__(self, item: Any) -> bool:
        """判断被包装对象是否包含指定元素。"""
        return item in self._inner

    def __repr__(self) -> str:
        """返回当前对象的调试字符串表示。"""
        return repr(self._inner)

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        """Return a proxy chain that auto-records token usage from structured output calls.

        Forces include_raw=True internally to always capture usage metadata from the
        raw AIMessage. If the caller did not request include_raw, the raw field is
        stripped from the returned result so the caller sees what it expected.
        """
        caller_wants_raw = bool(kwargs.get("include_raw", False))
        inner_chain = self._inner.with_structured_output(schema, **{**kwargs, "include_raw": True})
        stage = self._stage_prefix

        class _StructuredOutputProxy:
            """Proxy for a with_structured_output chain.
            Intercepts invoke/ainvoke to record token usage from the raw AIMessage.
            Note: stream/astream are not intercepted — token recording applies to
            invoke/ainvoke only."""

            def invoke(self, input_: Any, *args: Any, **kw: Any) -> Any:
                """同步执行 structured output 调用，并优先从 raw 响应记录 token 用量。"""
                result = inner_chain.invoke(input_, *args, **kw)
                if isinstance(result, dict) and "raw" in result:
                    raw = result.get("raw")
                    if raw is not None:
                        record_usage_from_response(raw, stage=stage)
                    if not caller_wants_raw:
                        if isinstance(result.get("parsing_error"), Exception):
                            raise result["parsing_error"]
                        return result.get("parsed")
                return result

            async def ainvoke(self, input_: Any, *args: Any, **kw: Any) -> Any:
                """异步执行 structured output 调用，并优先从 raw 响应记录 token 用量。"""
                result = await inner_chain.ainvoke(input_, *args, **kw)
                if isinstance(result, dict) and "raw" in result:
                    raw = result.get("raw")
                    if raw is not None:
                        record_usage_from_response(raw, stage=stage)
                    if not caller_wants_raw:
                        if isinstance(result.get("parsing_error"), Exception):
                            raise result["parsing_error"]
                        return result.get("parsed")
                return result

            def __getattr__(self, item: str) -> Any:
                """把未覆写的方法继续委托给原始 structured chain。"""
                return getattr(inner_chain, item)

        return _StructuredOutputProxy()


def get_llm(
    provider: str | None = None,
    model: str | None = None,
    *,
    inference: dict[str, Any] | None = None,
) -> BaseChatModel:
    """返回统一包装后的聊天模型对象。

    对业务层来说，最重要的不变量只有两个：
    1. 无论底层 provider 是谁，调用接口保持一致。
    2. 每次调用都会自动记录 token 用量和基础重试日志。
    """
    prov, resolved_model = _resolve_chat_provider_and_model(provider, model)
    log_event(logger, "llm.call.start", provider=prov, model=resolved_model)
    started = time.perf_counter()
    llm = _build_chat_model(prov, resolved_model, inference=inference)
    log_event(
        logger,
        "llm.call.success",
        provider=prov,
        model=resolved_model,
        latency_ms=round((time.perf_counter() - started) * 1000, 2),
    )
    stage_prefix = f"llm.{prov}.{resolved_model}"
    return _TrackedLLMProxy(llm, stage_prefix=stage_prefix)  # type: ignore[return-value]


def get_llm_with_fallback(
    provider: str | None,
    model: str | None,
    *,
    inference: dict[str, Any] | None = None,
) -> BaseChatModel:
    """兼容旧调用点的包装器。

    现在项目已经收敛到“单主模型连接”，这里保留只是为了减少旧代码迁移成本。
    """
    return get_llm(provider, model, inference=inference)


def get_embedding_model() -> OpenAIEmbeddings:
    """构建 embedding 连接。

    当前实现只支持 OpenAI-compatible embedding 协议。之所以和主模型分开，
    是因为生成模型可能走 Anthropic / Gemini，但向量检索仍常常依赖 OpenAI 兼容接口。
    """
    embedding = get_embedding_runtime()
    if not embedding.get("enabled"):
        raise RuntimeError("Embedding is disabled")

    model_name = str(embedding.get("model") or "").strip()
    if not model_name:
        raise RuntimeError("Embedding model is not configured")

    if embedding.get("reuse_primary_connection"):
        primary = get_primary_chat_runtime()
        if str(primary.get("resolved_protocol") or "") != "openai_compatible":
            raise RuntimeError("Embedding cannot reuse a non-OpenAI-compatible primary connection")
        return OpenAIEmbeddings(
            model=model_name,
            api_key=str(primary.get("api_key") or ""),
            base_url=str(primary.get("base_url") or "").strip() or "https://api.openai.com/v1",
        )

    if str(embedding.get("resolved_protocol") or "openai_compatible") != "openai_compatible":
        raise RuntimeError("Dedicated embedding connection currently only supports OpenAI-compatible protocol")
    return OpenAIEmbeddings(
        model=model_name,
        api_key=str(embedding.get("api_key") or ""),
        base_url=str(embedding.get("base_url") or "").strip() or "https://api.openai.com/v1",
    )


def embed_query(text: str) -> list[float] | None:
    """尽力而为地生成 query embedding，失败时返回 `None`。

    这里选择 soft-fail，而不是让所有调用方都因为 embedding 不可用而报错。
    对这个项目来说，向量检索是“增强项”，不是主生成链路的硬依赖。
    """
    if not text.strip():
        return None
    try:
        started = time.perf_counter()
        model = get_embedding_model()
        embedding = model.embed_query(text)
        runtime = get_embedding_runtime()
        log_event(
            logger,
            "embed.query.success",
            provider="primary" if runtime.get("reuse_primary_connection") else "openai_compatible",
            model=str(runtime.get("model") or ""),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        record_embedding_call()
        return embedding
    except Exception as exc:
        runtime = get_embedding_runtime()
        log_event(
            logger,
            "embed.query.fallback",
            level=logging.WARNING,
            provider="primary" if runtime.get("reuse_primary_connection") else "openai_compatible",
            model=str(runtime.get("model") or ""),
            error_class=type(exc).__name__,
            error_category="permanent",
            reason=str(exc)[:300],
        )
        return None
