"""Authorization package."""

from app.core.authz.types import Permission, Principal, ResourceContext
from app.core.authz.engine import authorize
from app.core.authz.deps import require_permission

__all__ = ["Permission", "Principal", "ResourceContext", "authorize", "require_permission"]
