"""Unified structured logging configuration."""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.core.trace import get_trace_id

_context_var: ContextVar[dict[str, Any]] = ContextVar("log_context", default={})
_SENSITIVE_KEYS = {"password", "token", "api_key", "authorization", "secret"}
_BASE_FIELDS = [
    "trace_id",
    "task_id",
    "novel_id",
    "user_id",
    "route",
    "method",
    "status_code",
    "latency_ms",
    "node",
    "chapter_num",
    "volume_no",
    "run_state",
    "error_code",
    "error_category",
    "retryable",
    "provider",
    "model",
    "attempt",
]


def get_log_redaction_level() -> str:
    return str(os.getenv("LOG_REDACTION_LEVEL", "minimal")).strip().lower()


def _mask_email(value: str) -> str:
    if "@" not in value:
        return value
    local, _, domain = value.partition("@")
    if len(local) <= 1:
        return "*@" + domain
    return f"{local[0]}***@{domain}"


def _redact_value(key: str, value: Any, level: str) -> Any:
    kl = key.lower()
    if kl in _SENSITIVE_KEYS or any(k in kl for k in _SENSITIVE_KEYS):
        return "***"
    if isinstance(value, dict):
        return {k: _redact_value(k, v, level) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(key, v, level) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(key, v, level) for v in value)
    if isinstance(value, str):
        if level in {"moderate", "strict"} and "@" in value:
            return _mask_email(value)
        if level == "strict" and len(value) > 128:
            return value[:64] + "...(redacted)"
    return value


def redact_fields(fields: dict[str, Any], level: str | None = None) -> dict[str, Any]:
    mode = level or get_log_redaction_level()
    return {k: _redact_value(k, v, mode) for k, v in fields.items()}


def set_log_context(**kwargs: Any) -> None:
    current = dict(_context_var.get() or {})
    for k, v in kwargs.items():
        if v is None:
            current.pop(k, None)
        else:
            current[k] = v
    _context_var.set(current)


def get_log_context() -> dict[str, Any]:
    return dict(_context_var.get() or {})


@contextmanager
def bind_log_context(**kwargs: Any) -> Iterator[None]:
    current = dict(_context_var.get() or {})
    merged = dict(current)
    for k, v in kwargs.items():
        if v is None:
            merged.pop(k, None)
        else:
            merged[k] = v
    token: Token = _context_var.set(merged)
    try:
        yield
    finally:
        _context_var.reset(token)


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # pragma: no cover - trivial
        ctx = get_log_context()
        trace_id = ctx.get("trace_id") or get_trace_id()
        setattr(record, "trace_id", trace_id)
        for key in _BASE_FIELDS:
            if key == "trace_id":
                continue
            if not hasattr(record, key):
                setattr(record, key, ctx.get(key))
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event_data = getattr(record, "event_data", {}) or {}
        if not isinstance(event_data, dict):
            event_data = {"detail": str(event_data)}
        fields = redact_fields(event_data)
        payload: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", None) or "log",
            "message": record.getMessage(),
        }
        for key in _BASE_FIELDS:
            payload[key] = getattr(record, key, None)
        payload.update(fields)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - fallback only
        event_data = getattr(record, "event_data", {}) or {}
        if not isinstance(event_data, dict):
            event_data = {"detail": str(event_data)}
        fields = redact_fields(event_data)
        chunks = [f"{k}={fields[k]}" for k in sorted(fields.keys())]
        head = f"[{record.levelname}] {record.name} event={getattr(record, 'event', 'log')} msg={record.getMessage()}"
        return head + (" " + " ".join(chunks) if chunks else "")


def setup_logging() -> None:
    level_name = str(os.getenv("LOG_LEVEL", "INFO")).upper()
    fmt = str(os.getenv("LOG_FORMAT", "json")).strip().lower()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    formatter = JsonFormatter() if fmt == "json" else TextFormatter()
    ctx_filter = ContextFilter()

    # Console handler (always on)
    console = logging.StreamHandler()
    console.addFilter(ctx_filter)
    console.setFormatter(formatter)
    root.addHandler(console)

    # File handler (rotating, ~50 MB per file, keep 10)
    log_dir = Path(os.getenv("LOG_DIR", "log"))
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "app.log",
            maxBytes=50 * 1024 * 1024,
            backupCount=10,
            encoding="utf-8",
        )
        file_handler.addFilter(ctx_filter)
        file_handler.setFormatter(JsonFormatter())  # always JSON in files
        root.addHandler(file_handler)
    except OSError as e:
        root.warning("setup_logging: cannot create log dir %s: %s", log_dir, e)

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "celery", "celery.app.trace"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True
        lg.setLevel(level)


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    message: str | None = None,
    **fields: Any,
) -> None:
    logger.log(level, message or event, extra={"event": event, "event_data": fields})


def now_ms() -> int:
    return int(time.time() * 1000)
