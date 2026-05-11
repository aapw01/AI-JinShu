"""Circuit breaker semantics for LLM fallback chain (§F)."""

from __future__ import annotations

import time

import pytest

from app.core import llm_circuit_breaker as cb


@pytest.fixture(autouse=True)
def _reset():
    cb.reset_breaker()
    yield
    cb.reset_breaker()


def test_default_select_returns_primary_closed():
    chosen, state = cb.select_endpoint("outliner")
    assert chosen == "primary"
    assert state == "closed"


def test_primary_opens_after_threshold_failures():
    cb.configure_stage(
        "outliner",
        cb.CircuitConfig(consecutive_failures_to_open=3, cooldown_seconds=600),
    )
    for _ in range(2):
        assert cb.record_call("outliner", "primary", success=False) == "closed"
    assert cb.record_call("outliner", "primary", success=False) == "open"


def test_select_returns_fallback_when_primary_open():
    cb.configure_stage(
        "writer",
        cb.CircuitConfig(consecutive_failures_to_open=2, cooldown_seconds=600),
    )
    cb.record_call("writer", "primary", success=False)
    cb.record_call("writer", "primary", success=False)
    chosen, state = cb.select_endpoint("writer")
    assert chosen == "fallback_a"
    assert state == "open"  # primary 处于 open


def test_select_falls_back_to_b_when_a_open():
    cb.configure_stage(
        "writer",
        cb.CircuitConfig(
            consecutive_failures_to_open=2,
            fallback_consecutive_failures_to_open=2,
            cooldown_seconds=600,
        ),
    )
    cb.record_call("writer", "primary", success=False)
    cb.record_call("writer", "primary", success=False)
    cb.record_call("writer", "fallback_a", success=False)
    cb.record_call("writer", "fallback_a", success=False)
    chosen, _ = cb.select_endpoint("writer")
    assert chosen == "fallback_b"


def test_success_closes_breaker():
    cb.configure_stage(
        "writer",
        cb.CircuitConfig(consecutive_failures_to_open=2, cooldown_seconds=600),
    )
    cb.record_call("writer", "primary", success=False)
    cb.record_call("writer", "primary", success=False)
    assert cb.get_breaker_state("writer", "primary") == "open"
    state = cb.record_call("writer", "primary", success=True)
    assert state == "closed"
    chosen, _ = cb.select_endpoint("writer")
    assert chosen == "primary"


def test_half_open_after_cooldown_then_probe():
    cb.configure_stage(
        "reviewer",
        cb.CircuitConfig(consecutive_failures_to_open=2, cooldown_seconds=0),
    )
    cb.record_call("reviewer", "primary", success=False)
    cb.record_call("reviewer", "primary", success=False)
    assert cb.get_breaker_state("reviewer", "primary") == "open"
    time.sleep(0.001)
    chosen, state = cb.select_endpoint("reviewer")
    assert chosen == "primary"
    assert state == "half_open"
    # half_open 探测失败 → 重回 open
    cb.record_call("reviewer", "primary", success=False)
    assert cb.get_breaker_state("reviewer", "primary") == "open"


def test_half_open_probe_success_closes():
    cb.configure_stage(
        "reviewer",
        cb.CircuitConfig(consecutive_failures_to_open=2, cooldown_seconds=0),
    )
    cb.record_call("reviewer", "primary", success=False)
    cb.record_call("reviewer", "primary", success=False)
    cb.select_endpoint("reviewer")  # 触发 half_open
    cb.record_call("reviewer", "primary", success=True)
    assert cb.get_breaker_state("reviewer", "primary") == "closed"


def test_all_open_with_exclusion_falls_back_to_primary():
    """全部 endpoint 熔断且都已尝试过 → 返回 primary（让真实失败抛出）。"""
    cb.configure_stage(
        "writer",
        cb.CircuitConfig(
            consecutive_failures_to_open=2,
            fallback_consecutive_failures_to_open=2,
            cooldown_seconds=600,
        ),
    )
    for ep in ("primary", "fallback_a", "fallback_b"):
        cb.record_call("writer", ep, success=False)
        cb.record_call("writer", ep, success=False)
    chosen, _ = cb.select_endpoint(
        "writer", exclude=("primary", "fallback_a", "fallback_b")
    )
    assert chosen == "primary"
