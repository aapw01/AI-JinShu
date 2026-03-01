import json
import logging

from app.core.logging_config import ContextFilter, JsonFormatter, redact_fields, set_log_context


def test_redact_sensitive_fields():
    out = redact_fields(
        {
            "password": "abc",
            "token": "123",
            "api_key": "secret",
            "authorization": "Bearer xxx",
            "safe": "ok",
        },
        level="minimal",
    )
    assert out["password"] == "***"
    assert out["token"] == "***"
    assert out["api_key"] == "***"
    assert out["authorization"] == "***"
    assert out["safe"] == "ok"


def test_json_formatter_contains_fixed_fields():
    logger = logging.getLogger("test.logging")
    rec = logger.makeRecord(
        name="test.logging",
        level=logging.INFO,
        fn="x.py",
        lno=1,
        msg="hello",
        args=(),
        exc_info=None,
        extra={
            "event": "test.event",
            "event_data": {"task_id": "t1", "novel_id": 1},
            "trace_id": "tr-1",
        },
    )
    payload = json.loads(JsonFormatter().format(rec))
    assert payload["event"] == "test.event"
    assert payload["trace_id"] == "tr-1"
    assert payload["task_id"] == "t1"
    assert payload["novel_id"] == 1
    assert "ts" in payload


def test_context_filter_injects_context_fields():
    logger = logging.getLogger("test.logging.ctx")
    rec = logger.makeRecord(
        name="test.logging.ctx",
        level=logging.INFO,
        fn="x.py",
        lno=1,
        msg="ctx",
        args=(),
        exc_info=None,
    )
    set_log_context(trace_id="trace-ctx", task_id="task-ctx", novel_id=42)
    ok = ContextFilter().filter(rec)
    assert ok is True
    assert getattr(rec, "trace_id") == "trace-ctx"
    assert getattr(rec, "task_id") == "task-ctx"
    assert getattr(rec, "novel_id") == 42

