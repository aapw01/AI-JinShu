"""Strategy fallback chain validator (Phase 0 §4.8).

Validates ``presets/strategies/<key>.yaml`` v2 schema:

```yaml
schema_version: 2
stages:
  outliner:
    primary: { provider_ref: "${default}", timeout_ms: 30000 }
    fallback_a: { provider_ref: "${fallback_premium}", timeout_ms: 30000 }
    fallback_b: { provider_ref: "${fallback_cheap}", timeout_ms: 20000 }
    circuit_breaker:
      consecutive_failures_to_open: 3
      cooldown_minutes: 60
      half_open_probe_ratio: 0.1
```

Phase 0 PR only ships the validator; the actual ``get_stage_runner`` plumbing
is left to the strategy refactor PR (#3a / #4 / #7 / #8 / #11 / #12 first
adopters).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StageEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_ref: str
    timeout_ms: int = Field(ge=1000, le=600_000)


class CircuitBreakerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consecutive_failures_to_open: int = Field(ge=1, le=10)
    cooldown_minutes: int = Field(ge=1, le=240)
    half_open_probe_ratio: float = Field(ge=0.0, le=1.0)


class StageFallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: StageEndpoint
    fallback_a: StageEndpoint
    fallback_b: StageEndpoint
    circuit_breaker: CircuitBreakerConfig


class StrategyV2(BaseModel):
    """Validator for strategies that opt into v2 fallback chain."""

    model_config = ConfigDict(extra="allow")  # 允许 v1 字段共存（migrate 期）

    schema_version: Literal[2]
    stages: dict[str, StageFallback]


def validate_strategy_v2(payload: dict) -> StrategyV2:
    """Public entry: raises ``pydantic.ValidationError`` on bad schema."""
    return StrategyV2.model_validate(payload)
