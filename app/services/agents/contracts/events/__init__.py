"""Event payload registry (Phase 0 §4.3.1).

Maps ``(agent_name, event_type)`` to a Pydantic model so ``emit_agent_event``
can validate ``payload`` before persisting. Each改造 PR adds its own entries
here; this file is the single registration surface.
"""

from __future__ import annotations

from app.services.agents.contracts.events.consistency_check import (
    DowngradePayload,
    ReviseAttemptPayload,
    SaveBlockedPayload,
)
from app.services.agents.contracts.events.fact_extractor import (
    FactFailurePayload,
    FactRetryPayload,
)

EVENT_PAYLOAD_REGISTRY: dict[tuple[str, str], type] = {
    ("consistency_check", "revise_attempt"): ReviseAttemptPayload,
    ("consistency_check", "save_blocked"): SaveBlockedPayload,
    ("consistency_check", "downgrade"): DowngradePayload,
    ("fact_extractor", "failure"): FactFailurePayload,
    ("fact_extractor", "retry"): FactRetryPayload,
}


def install_into_emitter() -> None:
    """Push static registry into ``app.services.agents.events`` runtime registry.

    Called from app startup (or each test) to wire the contracts. We do this
    in a function rather than at import time so test isolation can revert.
    """
    from app.services.agents import events as events_module

    for key, model_cls in EVENT_PAYLOAD_REGISTRY.items():
        events_module._EVENT_PAYLOAD_REGISTRY[key] = model_cls


__all__ = ["EVENT_PAYLOAD_REGISTRY", "install_into_emitter"]
