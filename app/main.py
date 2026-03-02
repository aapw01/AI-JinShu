"""FastAPI application entry point."""
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
    """Health check endpoint."""
    return {"status": "ok"}
