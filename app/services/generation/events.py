"""In-process synchronous event bus for generation side effects."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(slots=True)
class GenerationEvent:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)


class EventHandlerError(RuntimeError):
    """Raised when a required event handler fails."""


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[tuple[Callable[[GenerationEvent], None], bool]]] = {}

    def register(self, event_name: str, handler: Callable[[GenerationEvent], None], *, required: bool) -> None:
        self._handlers.setdefault(event_name, []).append((handler, required))

    def dispatch(self, event: GenerationEvent) -> dict[str, Any]:
        failures: list[dict[str, Any]] = []
        handlers = self._handlers.get(event.name, [])
        for handler, required in handlers:
            try:
                handler(event)
            except Exception as exc:
                failure = {
                    "event": event.name,
                    "handler": getattr(handler, "__name__", handler.__class__.__name__),
                    "required": required,
                    "error": str(exc),
                }
                failures.append(failure)
                if required:
                    raise EventHandlerError(str(exc)) from exc
        return {"failures": failures}
