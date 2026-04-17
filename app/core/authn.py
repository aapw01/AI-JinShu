"""Authentication helpers based on JWT access tokens."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging

import jwt
from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.authz.errors import unauthorized
from app.core.authz.types import Principal
from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging_config import log_event
from app.models.novel import User

logger = logging.getLogger(__name__)


def create_access_token(user_uuid: str, role: str, status: str = "active") -> str:
    """创建accessToken。"""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_uuid,
        "role": role,
        "status": status,
        "iss": settings.auth_jwt_issuer,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.auth_access_token_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.auth_jwt_secret, algorithm="HS256")


def _extract_token(request: Request) -> str | None:
    """提取Token。"""
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return cookie_token
    return None


def _decode_token(token: str) -> dict:
    """执行 decode token 相关辅助逻辑。"""
    settings = get_settings()
    return jwt.decode(
        token,
        settings.auth_jwt_secret,
        algorithms=["HS256"],
        issuer=settings.auth_jwt_issuer,
    )


def get_current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    """返回当前当前主体。"""
    token = _extract_token(request)
    if not token:
        log_event(logger, "auth.token.invalid", level=logging.WARNING, reason="missing_token")
        raise unauthorized("Missing token")
    try:
        payload = _decode_token(token)
    except jwt.PyJWTError:
        log_event(logger, "auth.token.invalid", level=logging.WARNING, reason="invalid_token")
        raise unauthorized("Invalid token")

    user_uuid = str(payload.get("sub") or "")
    if not user_uuid:
        log_event(logger, "auth.token.invalid", level=logging.WARNING, reason="missing_subject")
        raise unauthorized("Invalid token subject")

    user = db.execute(select(User).where(User.uuid == user_uuid)).scalar_one_or_none()
    if user:
        return Principal(
            user_uuid=user.uuid,
            role=user.role,
            status=user.status,
            is_authenticated=True,
        )

    log_event(logger, "auth.user_not_found", level=logging.WARNING, user_id=user_uuid)
    raise unauthorized("User not found")


def require_auth() -> callable:
    """执行 require auth 相关辅助逻辑。"""
    def _dep(principal: Principal = Depends(get_current_principal)) -> Principal:
        """确保当前请求对应的是一个可正常访问系统的激活用户。"""
        if not principal.is_authenticated:
            raise unauthorized("Unauthenticated")
        if principal.status == "disabled":
            raise unauthorized("User disabled")
        if principal.status == "pending_activation":
            raise unauthorized("Email not verified")
        if principal.status != "active":
            raise unauthorized("User inactive")
        return principal

    return _dep
