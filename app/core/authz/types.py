"""Authorization core types."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Permission(str, Enum):
    NOVEL_READ = "novel:read"
    NOVEL_CREATE = "novel:create"
    NOVEL_UPDATE = "novel:update"
    NOVEL_DELETE = "novel:delete"
    NOVEL_GENERATE = "novel:generate"
    NOVEL_REWRITE = "novel:rewrite"
    STORYBOARD_READ = "storyboard:read"
    STORYBOARD_CREATE = "storyboard:create"
    STORYBOARD_UPDATE = "storyboard:update"
    STORYBOARD_GENERATE = "storyboard:generate"
    STORYBOARD_FINALIZE = "storyboard:finalize"
    STORYBOARD_EXPORT = "storyboard:export"
    USER_READ = "user:read"
    USER_DISABLE = "user:disable"
    USER_QUOTA_UPDATE = "user:quota_update"
    SYSTEM_SETTINGS_READ = "system_settings:read"
    SYSTEM_SETTINGS_WRITE = "system_settings:write"


@dataclass(slots=True)
class Principal:
    user_uuid: str | None
    role: str
    status: str
    is_authenticated: bool


@dataclass(slots=True)
class ResourceContext:
    resource_type: str
    resource_id: str
    owner_uuid: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
