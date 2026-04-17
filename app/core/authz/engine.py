"""Authorization decision engine."""
from __future__ import annotations

from app.core.authz.policies import OWNER_SCOPED_PERMISSIONS, ROLE_BASE_PERMISSIONS
from app.core.authz.types import Permission, Principal, ResourceContext


class AuthorizationResult:
    """AuthorizationResult。"""
    def __init__(self, allowed: bool, reason: str):
        """初始化对象所需的运行时依赖。"""
        self.allowed = allowed
        self.reason = reason


def authorize(principal: Principal, permission: Permission, resource: ResourceContext | None = None) -> AuthorizationResult:
    """执行 authorize 相关辅助逻辑。"""
    if not principal.is_authenticated:
        return AuthorizationResult(False, "unauthenticated")

    if principal.status == "disabled":
        return AuthorizationResult(False, "user_status_disabled")
    if principal.status == "pending_activation":
        return AuthorizationResult(False, "user_status_pending_activation")
    elif principal.status != "active":
        return AuthorizationResult(False, f"user_status_{principal.status}")

    role_perms = ROLE_BASE_PERMISSIONS.get(principal.role, set())
    if permission not in role_perms:
        return AuthorizationResult(False, "permission_not_granted")

    if principal.role == "admin":
        return AuthorizationResult(True, "admin_allow")

    if permission in OWNER_SCOPED_PERMISSIONS:
        if resource is None:
            return AuthorizationResult(True, "owner_permission_no_resource")
        if resource.owner_uuid and principal.user_uuid == resource.owner_uuid:
            return AuthorizationResult(True, "owner_allow")
        return AuthorizationResult(False, "owner_mismatch")

    return AuthorizationResult(True, "role_allow")
