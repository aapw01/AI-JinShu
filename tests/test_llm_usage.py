from types import SimpleNamespace

from app.core.llm_usage import (
    begin_usage_session,
    end_usage_session,
    record_usage_from_response,
    snapshot_usage,
)


def test_usage_session_accumulates_usage_metadata():
    begin_usage_session("s1")
    resp = SimpleNamespace(usage_metadata={"input_tokens": 120, "output_tokens": 45, "total_tokens": 165})
    delta = record_usage_from_response(resp, stage="writer")
    assert delta["input_tokens"] == 120
    snap = snapshot_usage()
    assert snap["input_tokens"] == 120
    assert snap["output_tokens"] == 45
    assert snap["calls"] == 1
    out = end_usage_session()
    assert out["total_tokens"] == 165
    assert out["stages"]["writer"]["calls"] == 1


def test_usage_session_reads_response_metadata_token_usage():
    begin_usage_session("s2")
    resp = SimpleNamespace(response_metadata={"token_usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28}})
    record_usage_from_response(resp, stage="rewrite")
    out = end_usage_session()
    assert out["input_tokens"] == 20
    assert out["output_tokens"] == 8
    assert out["total_tokens"] == 28

