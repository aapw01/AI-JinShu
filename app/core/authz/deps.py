"""FastAPI dependencies for AuthZ."""
from __future__ import annotations

from collections.abc import Callable
import logging

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.authn import require_auth
from app.core.authz.engine import authorize
from app.core.authz.errors import forbidden
from app.core.authz.types import Permission, Principal, ResourceContext
from app.core.database import get_db
from app.core.logging_config import log_event

logger = logging.getLogger(__name__)

ResourceLoader = Callable[[Request, Session], ResourceContext | None]


def require_permission(permission: Permission, resource_loader: ResourceLoader | None = None):
    def _dep(
        request: Request,
        principal: Principal = Depends(require_auth()),
        db: Session = Depends(get_db),
    ) -> Principal:
        resource = resource_loader(request, db) if resource_loader else None
        result = authorize(principal, permission, resource)
        if not result.allowed:
            log_event(
                logger,
                "authz.denied",
                level=logging.WARNING,
                user_id=principal.user_uuid,
                role=principal.role,
                permission=permission.value,
                resource_type=resource.resource_type if resource else None,
                resource_id=resource.resource_id if resource else None,
                reason=result.reason,
            )
            raise forbidden("Permission denied")
        return principal

    return _dep
