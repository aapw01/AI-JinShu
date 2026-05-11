"""Strategy fallback Pydantic validator (Phase 0 §4.8)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.strategy_fallback import validate_strategy_v2


_VALID = {
    "schema_version": 2,
    "stages": {
        "outliner": {
            "primary": {"provider_ref": "${default}", "timeout_ms": 30000},
            "fallback_a": {"provider_ref": "${fallback_premium}", "timeout_ms": 30000},
            "fallback_b": {"provider_ref": "${fallback_cheap}", "timeout_ms": 20000},
            "circuit_breaker": {
                "consecutive_failures_to_open": 3,
                "cooldown_minutes": 60,
                "half_open_probe_ratio": 0.1,
            },
        }
    },
}


def test_valid_payload_parses():
    parsed = validate_strategy_v2(_VALID)
    assert parsed.schema_version == 2
    stage = parsed.stages["outliner"]
    assert stage.primary.timeout_ms == 30000
    assert stage.circuit_breaker.cooldown_minutes == 60


def test_missing_fallback_b_rejected():
    bad = {**_VALID}
    bad_stages = dict(_VALID["stages"])
    stage = dict(bad_stages["outliner"])
    stage.pop("fallback_b")
    bad_stages["outliner"] = stage
    bad["stages"] = bad_stages
    with pytest.raises(ValidationError):
        validate_strategy_v2(bad)


def test_invalid_timeout_rejected():
    bad = {
        "schema_version": 2,
        "stages": {
            "outliner": {
                "primary": {"provider_ref": "${default}", "timeout_ms": 100},  # < 1000
                "fallback_a": {"provider_ref": "${fallback}", "timeout_ms": 30000},
                "fallback_b": {"provider_ref": "${fallback}", "timeout_ms": 30000},
                "circuit_breaker": {
                    "consecutive_failures_to_open": 3,
                    "cooldown_minutes": 60,
                    "half_open_probe_ratio": 0.1,
                },
            }
        },
    }
    with pytest.raises(ValidationError):
        validate_strategy_v2(bad)


def test_circuit_breaker_ratio_bounds():
    bad = {
        "schema_version": 2,
        "stages": {
            "outliner": {
                "primary": {"provider_ref": "x", "timeout_ms": 30000},
                "fallback_a": {"provider_ref": "x", "timeout_ms": 30000},
                "fallback_b": {"provider_ref": "x", "timeout_ms": 30000},
                "circuit_breaker": {
                    "consecutive_failures_to_open": 3,
                    "cooldown_minutes": 60,
                    "half_open_probe_ratio": 1.5,
                },
            }
        },
    }
    with pytest.raises(ValidationError):
        validate_strategy_v2(bad)


def test_wrong_schema_version_rejected():
    with pytest.raises(ValidationError):
        validate_strategy_v2({"schema_version": 1, "stages": {}})
