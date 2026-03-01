"""Static RBAC policy map."""
from __future__ import annotations

from app.core.authz.types import Permission

ROLE_BASE_PERMISSIONS: dict[str, set[Permission]] = {
    "admin": set(Permission),
    "user": {
        Permission.NOVEL_READ,
        Permission.NOVEL_CREATE,
        Permission.NOVEL_UPDATE,
        Permission.NOVEL_DELETE,
        Permission.NOVEL_GENERATE,
        Permission.NOVEL_REWRITE,
        Permission.STORYBOARD_READ,
        Permission.STORYBOARD_CREATE,
        Permission.STORYBOARD_UPDATE,
        Permission.STORYBOARD_GENERATE,
        Permission.STORYBOARD_FINALIZE,
        Permission.STORYBOARD_EXPORT,
    },
}

OWNER_SCOPED_PERMISSIONS: set[Permission] = {
    Permission.NOVEL_READ,
    Permission.NOVEL_UPDATE,
    Permission.NOVEL_DELETE,
    Permission.NOVEL_GENERATE,
    Permission.NOVEL_REWRITE,
    Permission.STORYBOARD_READ,
    Permission.STORYBOARD_UPDATE,
    Permission.STORYBOARD_GENERATE,
    Permission.STORYBOARD_FINALIZE,
    Permission.STORYBOARD_EXPORT,
}
