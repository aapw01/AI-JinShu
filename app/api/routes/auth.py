"""Authentication routes."""
from __future__ import annotations

from datetime import timedelta, timezone
import logging

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.authn import create_access_token, require_auth
from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging_config import log_event
from app.core.authz.types import Principal
from app.models.novel import EmailVerificationToken, PasswordResetToken, User
from app.schemas.auth import (
    AuthTokenResponse,
    ForgotPasswordRequest,
    LoginRequest,
    RegisterRequest,
    ResetPasswordRequest,
    VerifyEmailRequest,
    VerifyEmailRequestSend,
)
from app.services.mailer import can_send_email, send_reset_password_email, send_verify_email
from app.services.quota import ensure_user_quota
from app.services.security import (
    build_reset_link,
    build_verify_link,
    future_minutes,
    hash_password,
    new_raw_token,
    token_hash,
    utc_now,
    validate_password_complexity,
    verify_password,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _set_auth_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        domain=settings.auth_cookie_domain,
        max_age=settings.auth_access_token_minutes * 60,
        path="/",
    )


def _clear_auth_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie("access_token", domain=settings.auth_cookie_domain, path="/")


def _user_payload(u: User) -> dict:
    return {
        "uuid": u.uuid,
        "email": u.email,
        "role": u.role,
        "status": u.status,
        "email_verified": bool(u.email_verified_at),
    }


def _is_email_like(email: str) -> bool:
    value = (email or "").strip()
    return "@" in value and "." in value.split("@")[-1]


def _purge_old_tokens(db: Session, user_id: int) -> None:
    now = utc_now()
    db.execute(
        update(EmailVerificationToken)
        .where(EmailVerificationToken.user_id == user_id, EmailVerificationToken.used_at.is_(None), EmailVerificationToken.expires_at < now)
        .values(used_at=now)
    )
    db.execute(
        update(PasswordResetToken)
        .where(PasswordResetToken.user_id == user_id, PasswordResetToken.used_at.is_(None), PasswordResetToken.expires_at < now)
        .values(used_at=now)
    )


@router.post("/register")
def register(data: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    if settings.auth_require_email_verification and not can_send_email():
        log_event(
            logger,
            "auth.register.failed",
            level=logging.ERROR,
            reason="mail_service_not_configured",
            email=data.email,
        )
        return Response(
            status_code=503,
            content='{"detail":"Email verification is enabled but mail service is not configured"}',
            media_type="application/json",
        )
    if not _is_email_like(data.email):
        log_event(logger, "auth.register.failed", level=logging.WARNING, reason="invalid_email", email=data.email)
        return Response(status_code=400, content='{"detail":"Invalid email"}', media_type="application/json")
    ok, message = validate_password_complexity(data.password)
    if not ok:
        log_event(logger, "auth.register.failed", level=logging.WARNING, reason="weak_password", email=data.email)
        return Response(status_code=400, content=f'{{"detail":"{message}"}}', media_type="application/json")

    existed = db.execute(select(User).where(User.email == data.email.lower())).scalar_one_or_none()
    if existed:
        log_event(logger, "auth.register.failed", level=logging.WARNING, reason="email_exists", email=data.email.lower())
        return Response(status_code=409, content='{"detail":"Email already registered"}', media_type="application/json")

    count = db.execute(select(func.count()).select_from(User)).scalar_one()
    first_user = int(count or 0) == 0
    now = utc_now()
    role = "admin" if first_user else "user"
    status = "pending_activation" if settings.auth_require_email_verification else "active"
    user = User(
        email=data.email.lower(),
        password_hash=hash_password(data.password),
        role=role,
        status=status,
        email_verified_at=None if settings.auth_require_email_verification else now,
        password_updated_at=now,
    )
    try:
        db.add(user)
        db.flush()
    except IntegrityError:
        db.rollback()
        log_event(logger, "auth.register.failed", level=logging.WARNING, reason="email_exists_race", email=data.email.lower())
        return Response(status_code=409, content='{"detail":"Email already registered"}', media_type="application/json")
    ensure_user_quota(db, user)

    if settings.auth_require_email_verification:
        raw = new_raw_token()
        db.add(
            EmailVerificationToken(
                user_id=user.id,
                token_hash=token_hash(raw),
                expires_at=future_minutes(settings.auth_verify_token_minutes),
            )
        )
        db.commit()
        send_verify_email(user.email, build_verify_link(raw))
        log_event(logger, "auth.register.pending_verification", email=user.email, user_id=user.uuid, role=role)
        return {"ok": True, "message": "注册成功，请查收邮件激活账号"}

    token = create_access_token(user.uuid, role=user.role, status=user.status)
    db.commit()
    _set_auth_cookie(response, token)
    log_event(logger, "auth.register.success", email=user.email, user_id=user.uuid, role=user.role)
    return AuthTokenResponse(access_token=token, user=_user_payload(user))


@router.post("/login", response_model=AuthTokenResponse)
def login(data: LoginRequest, response: Response, db: Session = Depends(get_db)):
    settings = get_settings()
    if not _is_email_like(data.email):
        log_event(logger, "auth.login.failed", level=logging.WARNING, reason="invalid_email", email=data.email)
        return Response(status_code=400, content='{"detail":"Invalid email"}', media_type="application/json")
    now = utc_now()
    user = db.execute(select(User).where(User.email == data.email.lower())).scalar_one_or_none()
    if not user:
        log_event(logger, "auth.login.failed", level=logging.WARNING, reason="invalid_credentials", email=data.email.lower())
        return Response(status_code=401, content='{"detail":"Invalid email or password"}', media_type="application/json")

    if user.locked_until:
        lock_dt = user.locked_until
        if lock_dt.tzinfo is None:
            lock_dt = lock_dt.replace(tzinfo=timezone.utc)
        if lock_dt > now:
            log_event(logger, "auth.login.failed", level=logging.WARNING, reason="locked", email=user.email, user_id=user.uuid)
            return Response(status_code=423, content='{"detail":"Account temporarily locked"}', media_type="application/json")
        user.locked_until = None
        user.failed_login_count = 0

    if user.status == "disabled":
        log_event(logger, "auth.login.failed", level=logging.WARNING, reason="disabled", email=user.email, user_id=user.uuid)
        return Response(status_code=403, content='{"detail":"User disabled"}', media_type="application/json")

    if not verify_password(data.password, user.password_hash):
        user.failed_login_count = int(user.failed_login_count or 0) + 1
        if user.failed_login_count >= settings.auth_login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.auth_login_lock_minutes)
            user.failed_login_count = 0
        db.commit()
        log_event(logger, "auth.login.failed", level=logging.WARNING, reason="invalid_password", email=user.email, user_id=user.uuid)
        return Response(status_code=401, content='{"detail":"Invalid email or password"}', media_type="application/json")

    if user.status == "pending_activation":
        log_event(logger, "auth.login.failed", level=logging.WARNING, reason="email_unverified", email=user.email, user_id=user.uuid)
        return Response(status_code=403, content='{"detail":"Email not verified"}', media_type="application/json")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    if user.status == "pending_activation" and user.email_verified_at:
        user.status = "active"
    db.commit()

    token = create_access_token(user.uuid, role=user.role, status=user.status)
    _set_auth_cookie(response, token)
    log_event(logger, "auth.login.success", email=user.email, user_id=user.uuid, role=user.role)
    return AuthTokenResponse(access_token=token, user=_user_payload(user))


@router.post("/logout")
def logout(response: Response):
    _clear_auth_cookie(response)
    log_event(logger, "auth.logout")
    return {"ok": True}


@router.get("/me")
def me(principal: Principal = Depends(require_auth()), db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.uuid == principal.user_uuid)).scalar_one_or_none()
    if not user:
        return Response(status_code=404, content='{"detail":"User not found"}', media_type="application/json")
    return {"user": _user_payload(user)}


@router.post("/verify-email/request")
def request_verify_email(data: VerifyEmailRequestSend, db: Session = Depends(get_db)):
    if not _is_email_like(data.email):
        return {"ok": True}
    user = db.execute(select(User).where(User.email == data.email.lower())).scalar_one_or_none()
    if not user:
        return {"ok": True}
    if user.email_verified_at:
        return {"ok": True, "message": "邮箱已验证"}
    _purge_old_tokens(db, user.id)
    raw = new_raw_token()
    db.add(
        EmailVerificationToken(
            user_id=user.id,
            token_hash=token_hash(raw),
            expires_at=future_minutes(get_settings().auth_verify_token_minutes),
        )
    )
    db.commit()
    send_verify_email(user.email, build_verify_link(raw))
    return {"ok": True}


@router.post("/verify-email/confirm")
def confirm_verify_email(data: VerifyEmailRequest, db: Session = Depends(get_db)):
    now = utc_now()
    row = db.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == token_hash(data.token),
            EmailVerificationToken.used_at.is_(None),
        )
    ).scalar_one_or_none()
    if not row or row.expires_at < now:
        return Response(status_code=400, content='{"detail":"Invalid or expired token"}', media_type="application/json")
    user = db.execute(select(User).where(User.id == row.user_id)).scalar_one_or_none()
    if not user:
        return Response(status_code=404, content='{"detail":"User not found"}', media_type="application/json")
    row.used_at = now
    user.email_verified_at = now
    if user.status == "pending_activation":
        user.status = "active"
    db.commit()
    return {"ok": True}


@router.post("/password/forgot")
def forgot_password(data: ForgotPasswordRequest, db: Session = Depends(get_db)):
    if not _is_email_like(data.email):
        return {"ok": True}
    user = db.execute(select(User).where(User.email == data.email.lower())).scalar_one_or_none()
    if not user:
        return {"ok": True}
    _purge_old_tokens(db, user.id)
    raw = new_raw_token()
    db.add(
        PasswordResetToken(
            user_id=user.id,
            token_hash=token_hash(raw),
            expires_at=future_minutes(get_settings().auth_reset_token_minutes),
        )
    )
    db.commit()
    send_reset_password_email(user.email, build_reset_link(raw))
    return {"ok": True}


@router.post("/password/reset")
def reset_password(data: ResetPasswordRequest, db: Session = Depends(get_db)):
    ok, message = validate_password_complexity(data.new_password)
    if not ok:
        return Response(status_code=400, content=f'{{"detail":"{message}"}}', media_type="application/json")
    now = utc_now()
    row = db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash(data.token),
            PasswordResetToken.used_at.is_(None),
        )
    ).scalar_one_or_none()
    if not row or row.expires_at < now:
        return Response(status_code=400, content='{"detail":"Invalid or expired token"}', media_type="application/json")
    user = db.execute(select(User).where(User.id == row.user_id)).scalar_one_or_none()
    if not user:
        return Response(status_code=404, content='{"detail":"User not found"}', media_type="application/json")
    row.used_at = now
    user.password_hash = hash_password(data.new_password)
    user.password_updated_at = now
    user.failed_login_count = 0
    user.locked_until = None
    db.commit()
    return {"ok": True}
