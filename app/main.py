"""FastAPI 应用入口。

模块职责：
- 创建 API 应用对象，注册所有路由与中间件。
- 在进程启动时完成日志初始化、生产环境配置校验、LLM 运行时配置透出。
- 把 trace_id 贯穿到一次 HTTP 请求的整个日志链路里。

系统位置：
- 上游是 Uvicorn / FastAPI 启动流程。
- 下游是 `app/api/routes/*` 中的所有业务路由，以及统一日志/追踪设施。

面试可讲点：
- 为什么把生产环境校验放在 import 阶段尽早失败。
- 为什么要把 `X-Trace-Id` 从入口一路透传到响应头和日志里。
- 为什么 CORS、日志、路由注册要在入口层集中管理。
"""
import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from app.api.routes import novels, chapters, export, presets, generation, longform, rewrite, admin, auth, account, storyboards
from app.core.config import get_settings, validate_settings_for_production
from app.core.logging_config import bind_log_context, log_event, setup_logging
from app.core.trace import new_trace_id, set_trace_id

setup_logging()
logger = logging.getLogger(__name__)

validate_settings_for_production()


def _log_llm_config():
    """在启动时打印最终生效的 LLM 连接配置。

    这里刻意只读取环境级配置，不依赖数据库。原因是启动阶段最怕
    “服务其实起不来，但日志里又看不到模型连接到底指向哪里”。
    这段日志能让你快速回答：
    1. 当前主模型走的是哪个 provider / adapter。
    2. base_url 是显式配置还是自动推断。
    3. embedding 是否会因为复用主连接而在运行时失败。
    """
    s = get_settings()
    provider = (s.llm_provider or "openai").strip().lower()
    model = s.llm_model or "gpt-4o-mini"
    base_url = (s.llm_base_url or "").rstrip("/") or "(provider default)"
    api_key = s.llm_api_key or ""
    masked_key = f"{api_key[:4]}...{api_key[-4:]}" if len(api_key) > 8 else ("(empty)" if not api_key else "****")

    protocol_override = (s.llm_protocol_override or "").strip().lower() or None
    if protocol_override and protocol_override not in ("openai_compatible", "gemini", "anthropic"):
        protocol_override = None

    if protocol_override:
        adapter, adapter_source = protocol_override, "env_override"
    elif s.llm_base_url:
        adapter, adapter_source = "openai_compatible", "auto_infer"
    elif provider == "gemini":
        adapter, adapter_source = "gemini", "auto_native"
    elif provider == "anthropic":
        adapter, adapter_source = "anthropic", "auto_native"
    else:
        adapter, adapter_source = "openai_compatible", "auto_native"

    embedding_warning = None
    if s.embedding_enabled and s.embedding_reuse_primary_connection and adapter != "openai_compatible":
        embedding_warning = (
            f"embedding_reuse_primary_connection=true but primary adapter is '{adapter}'; "
            "embedding will fail at runtime — set EMBEDDING_REUSE_PRIMARY_CONNECTION=false "
            "or switch adapter to openai_compatible"
        )

    log_event(
        logger,
        "llm.config.startup",
        provider=provider,
        model=model,
        adapter=adapter,
        adapter_source=adapter_source,
        protocol_override=protocol_override or "(auto)",
        base_url=base_url,
        api_key=masked_key,
        embedding_enabled=bool(s.embedding_enabled),
        embedding_model=s.embedding_model or "(not set)",
        embedding_reuse_primary_connection=bool(s.embedding_reuse_primary_connection),
        embedding_warning=embedding_warning,
    )


_log_llm_config()

app = FastAPI(title="AI-JinShu API", version="0.1.0")

_settings = get_settings()
_cors_origins = [o.strip() for o in _settings.auth_frontend_base_url.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(novels.router, prefix="/api/novels", tags=["novels"])
app.include_router(chapters.router, prefix="/api/novels", tags=["chapters"])
app.include_router(export.router, prefix="/api/novels", tags=["export"])
app.include_router(presets.router, prefix="/api/presets", tags=["presets"])
app.include_router(generation.router, prefix="/api/novels", tags=["generation"])
app.include_router(longform.router, prefix="/api/novels", tags=["longform"])
app.include_router(rewrite.router, prefix="/api/novels", tags=["rewrite"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(account.router, prefix="/api/account", tags=["account"])
app.include_router(storyboards.router, prefix="/api/storyboards", tags=["storyboards"])


@app.middleware("http")
async def trace_id_middleware(request: Request, call_next):
    """为每个 HTTP 请求绑定 trace_id，并统一记录请求起止日志。

    设计目的不是“多打一条日志”，而是给一次请求建立稳定的观察主键。
    这样后端路由、服务层、Celery worker 只要沿用同一个 trace_id，
    排查一次失败生成任务时就能把 API 入口和异步执行阶段串起来。
    """
    trace_id = request.headers.get("X-Trace-Id") or new_trace_id()
    started = time.perf_counter()
    set_trace_id(trace_id)
    request.state.trace_id = trace_id
    with bind_log_context(trace_id=trace_id, route=request.url.path, method=request.method):
        log_event(
            logger,
            "api.request.received",
            trace_id=trace_id,
            route=request.url.path,
            method=request.method,
        )
        try:
            response: Response = await call_next(request)
        except Exception as exc:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            log_event(
                logger,
                "api.request.failed",
                level=logging.ERROR,
                message="API request failed",
                trace_id=trace_id,
                route=request.url.path,
                method=request.method,
                latency_ms=elapsed_ms,
                error_code="UNHANDLED_EXCEPTION",
                error_category="permanent",
                error_class=type(exc).__name__,
            )
            raise
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Trace-Id"] = trace_id
        log_event(
            logger,
            "api.request.completed",
            trace_id=trace_id,
            route=request.url.path,
            method=request.method,
            status_code=response.status_code,
            latency_ms=elapsed_ms,
        )
        if elapsed_ms > int(get_settings().log_slow_threshold_ms or 1500):
            log_event(
                logger,
                "api.request.slow",
                level=logging.WARNING,
                trace_id=trace_id,
                route=request.url.path,
                method=request.method,
                status_code=response.status_code,
                latency_ms=elapsed_ms,
                threshold_ms=int(get_settings().log_slow_threshold_ms or 1500),
            )
        return response


@app.get("/health")
def health():
    """返回最小健康检查结果，供负载均衡与部署探针使用。"""
    return {"status": "ok"}
