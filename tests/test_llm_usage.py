from types import SimpleNamespace

from app.core.llm_usage import (
    begin_usage_session,
    end_usage_session,
    record_embedding_call,
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


def test_begin_session_with_base_tokens():
    """Resume scenario: session starts at historical token count, new calls accumulate on top."""
    begin_usage_session("resume-test", base_input=1000, base_output=500)
    resp = SimpleNamespace(usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150})
    record_usage_from_response(resp, stage="writer")
    snap = snapshot_usage()
    assert snap["input_tokens"] == 1100   # 1000 base + 100 new
    assert snap["output_tokens"] == 550   # 500 base + 50 new
    assert snap["calls"] == 1             # only new calls counted
    assert snap["total_tokens"] == 1650   # (1000+500) base + (100+50) new
    end_usage_session()


def test_record_embedding_call_tracks_count():
    begin_usage_session("embed-test")
    record_embedding_call()
    record_embedding_call()
    snap = snapshot_usage()
    assert snap["embedding_calls"] == 2
    out = end_usage_session()
    assert out["embedding_calls"] == 2


def test_session_without_base_defaults_to_zero():
    begin_usage_session("no-base")
    snap = snapshot_usage()
    assert snap["input_tokens"] == 0
    assert snap["embedding_calls"] == 0
    end_usage_session()

