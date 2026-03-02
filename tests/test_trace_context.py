from uuid import uuid4

from app.core.trace import get_trace_id, set_trace_id


def test_set_trace_id_tolerates_non_string_values():
    set_trace_id(None)
    assert get_trace_id() is None

    set_trace_id(12345)
    assert get_trace_id() == "12345"

    uid = uuid4()
    set_trace_id(uid)
    assert get_trace_id() == str(uid)

    set_trace_id("  trace-abc  ")
    assert get_trace_id() == "trace-abc"

    set_trace_id("   ")
    assert get_trace_id() is None

    set_trace_id(None)
