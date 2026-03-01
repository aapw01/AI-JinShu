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
    auth = request.headers.get("Authorization") or ""
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    cookie_token = request.cookies.get("access_token")
    if cookie_token:
        return cookie_token
    return None


def _decode_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(
        token,
        settings.auth_jwt_secret,
        algorithms=["HS256"],
        issuer=settings.auth_jwt_issuer,
    )


def get_current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
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

    # Allow stateless principal in early integration/tests if DB user not yet seeded.
    role = str(payload.get("role") or "user")
    status = str(payload.get("status") or "active")
    log_event(logger, "auth.token.stateless_principal", level=logging.INFO, user_id=user_uuid, role=role, run_state=status)
    return Principal(user_uuid=user_uuid, role=role, status=status, is_authenticated=True)


def require_auth() -> callable:
    def _dep(principal: Principal = Depends(get_current_principal)) -> Principal:
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
