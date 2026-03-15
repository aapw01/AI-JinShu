"""Unified task status/progress DTOs shared across generation, rewrite, and storyboard."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TaskStatusBase(BaseModel):
    """Common status fields shared by all creation task types."""
    status: str
    run_state: str | None = None
    current_phase: str | None = None
    progress: float = 0.0
    message: str | None = None
    error: str | None = None
    error_code: str | None = None
    error_category: str | None = None
    retryable: bool | None = None
    eta_seconds: int | None = None
    eta_label: str | None = None


class TaskProgressEvent(BaseModel):
    """Unified progress event emitted by all pipeline types."""
    task_id: str | None = None
    resource_type: str  # novel | rewrite_request | storyboard_project
    resource_id: int
    step: str
    chapter: int = 0
    progress: float = 0.0
    message: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class TaskResultDTO(BaseModel):
    """Result metadata attached when a task reaches a terminal state."""
    token_usage_input: int = 0
    token_usage_output: int = 0
    estimated_cost: float = 0.0
    final_report: dict[str, Any] | None = None


class TaskErrorDTO(BaseModel):
    """Structured error payload for failed tasks."""
    code: str | None = None
    category: str | None = None
    retryable: bool | None = None
    message: str | None = None
