"""Authentication routes."""
from __future__ import annotations

from datetime import timedelta, timezone
import logging

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.api_errors import error_response

from app.core.authn import create_access_token, require_auth
from app.core.config import get_settings
from app.core.database import get_db
from app.core.logging_config import log_event
from app.core.authz.types import Principal
from app.models.novel import EmailVerificationToken, PasswordResetToken, User
from app.schemas.auth import (
    AuthTokenResponse,
    ChangePasswordRequest,
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
    """设置认证cookie。"""
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
    """执行 clear auth cookie 相关辅助逻辑。"""
    settings = get_settings()
    response.delete_cookie("access_token", domain=settings.auth_cookie_domain, path="/")


def _user_payload(u: User) -> dict:
    """执行 user payload 相关辅助逻辑。"""
    return {
        "uuid": u.uuid,
        "email": u.email,
        "role": u.role,
        "status": u.status,
        "email_verified": bool(u.email_verified_at),
    }


def _is_email_like(email: str) -> bool:
    """执行 is email like 相关辅助逻辑。"""
    value = (email or "").strip()
    return "@" in value and "." in value.split("@")[-1]


def _purge_old_tokens(db: Session, user_id: int) -> None:
    """执行 purge old tokens 相关辅助逻辑。"""
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
    """执行 register 相关辅助逻辑。"""
    settings = get_settings()
    if settings.auth_require_email_verification and not can_send_email():
        log_event(
            logger,
            "auth.register.failed",
            level=logging.ERROR,
            reason="mail_service_not_configured",
            email=data.email,
        )
        return error_response(
            503,
            "mail_service_not_configured",
            "Email verification is enabled but mail service is not configured",
        )
    if not _is_email_like(data.email):
        log_event(logger, "auth.register.failed", level=logging.WARNING, reason="invalid_email", email=data.email)
        return error_response(400, "invalid_email", "Invalid email")
    ok, message = validate_password_complexity(data.password)
    if not ok:
        log_event(logger, "auth.register.failed", level=logging.WARNING, reason="weak_password", email=data.email)
        return error_response(400, "weak_password", message)

    existed = db.execute(select(User).where(User.email == data.email.lower())).scalar_one_or_none()
    if existed:
        log_event(logger, "auth.register.failed", level=logging.WARNING, reason="email_exists", email=data.email.lower())
        return error_response(409, "email_already_registered", "Email already registered")

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
        return error_response(409, "email_already_registered", "Email already registered")
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
    """执行 login 相关辅助逻辑。"""
    settings = get_settings()
    if not _is_email_like(data.email):
        log_event(logger, "auth.login.failed", level=logging.WARNING, reason="invalid_email", email=data.email)
        return error_response(400, "invalid_email", "Invalid email")
    now = utc_now()
    user = db.execute(select(User).where(User.email == data.email.lower())).scalar_one_or_none()
    if not user:
        log_event(logger, "auth.login.failed", level=logging.WARNING, reason="invalid_credentials", email=data.email.lower())
        return error_response(401, "invalid_credentials", "Invalid email or password")

    if user.locked_until:
        lock_dt = user.locked_until
        if lock_dt.tzinfo is None:
            lock_dt = lock_dt.replace(tzinfo=timezone.utc)
        if lock_dt > now:
            log_event(logger, "auth.login.failed", level=logging.WARNING, reason="locked", email=user.email, user_id=user.uuid)
            return error_response(423, "account_temporarily_locked", "Account temporarily locked")
        user.locked_until = None
        user.failed_login_count = 0

    if user.status == "disabled":
        log_event(logger, "auth.login.failed", level=logging.WARNING, reason="disabled", email=user.email, user_id=user.uuid)
        return error_response(403, "user_disabled", "User disabled")

    if not verify_password(data.password, user.password_hash):
        user.failed_login_count = int(user.failed_login_count or 0) + 1
        if user.failed_login_count >= settings.auth_login_max_failures:
            user.locked_until = now + timedelta(minutes=settings.auth_login_lock_minutes)
            user.failed_login_count = 0
        db.commit()
        log_event(logger, "auth.login.failed", level=logging.WARNING, reason="invalid_password", email=user.email, user_id=user.uuid)
        return error_response(401, "invalid_credentials", "Invalid email or password")

    if user.status == "pending_activation":
        log_event(logger, "auth.login.failed", level=logging.WARNING, reason="email_unverified", email=user.email, user_id=user.uuid)
        return error_response(403, "email_not_verified", "Email not verified")

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
    """执行 logout 相关辅助逻辑。"""
    _clear_auth_cookie(response)
    log_event(logger, "auth.logout")
    return {"ok": True}


@router.get("/me")
def me(principal: Principal = Depends(require_auth()), db: Session = Depends(get_db)):
    """执行 me 相关辅助逻辑。"""
    user = db.execute(select(User).where(User.uuid == principal.user_uuid)).scalar_one_or_none()
    if not user:
        return error_response(404, "user_not_found", "User not found")
    return {"user": _user_payload(user)}


@router.post("/verify-email/request")
def request_verify_email(data: VerifyEmailRequestSend, db: Session = Depends(get_db)):
    """执行 request verify email 相关辅助逻辑。"""
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
    """执行 confirm verify email 相关辅助逻辑。"""
    now = utc_now()
    row = db.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == token_hash(data.token),
            EmailVerificationToken.used_at.is_(None),
        )
    ).scalar_one_or_none()
    if not row or row.expires_at < now:
        return error_response(400, "invalid_or_expired_token", "Invalid or expired token")
    user = db.execute(select(User).where(User.id == row.user_id)).scalar_one_or_none()
    if not user:
        return error_response(404, "user_not_found", "User not found")
    row.used_at = now
    user.email_verified_at = now
    if user.status == "pending_activation":
        user.status = "active"
    db.commit()
    return {"ok": True}


@router.post("/password/forgot")
def forgot_password(data: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """执行 forgot password 相关辅助逻辑。"""
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
    """执行 reset password 相关辅助逻辑。"""
    ok, message = validate_password_complexity(data.new_password)
    if not ok:
        return error_response(400, "weak_password", message)
    now = utc_now()
    row = db.execute(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == token_hash(data.token),
            PasswordResetToken.used_at.is_(None),
        )
    ).scalar_one_or_none()
    if not row or row.expires_at < now:
        return error_response(400, "invalid_or_expired_token", "Invalid or expired token")
    user = db.execute(select(User).where(User.id == row.user_id)).scalar_one_or_none()
    if not user:
        return error_response(404, "user_not_found", "User not found")
    row.used_at = now
    user.password_hash = hash_password(data.new_password)
    user.password_updated_at = now
    user.failed_login_count = 0
    user.locked_until = None
    db.commit()
    return {"ok": True}


@router.post("/password/change")
def change_password(
    data: ChangePasswordRequest,
    principal: Principal = Depends(require_auth()),
    db: Session = Depends(get_db),
):
    """执行 change password 相关辅助逻辑。"""
    user = db.execute(select(User).where(User.uuid == principal.user_uuid)).scalar_one_or_none()
    if not user:
        return error_response(404, "user_not_found", "User not found")
    if not verify_password(data.current_password, user.password_hash):
        return error_response(400, "current_password_incorrect", "Current password is incorrect")
    ok, message = validate_password_complexity(data.new_password)
    if not ok:
        return error_response(400, "weak_password", message)
    user.password_hash = hash_password(data.new_password)
    user.password_updated_at = utc_now()
    user.failed_login_count = 0
    user.locked_until = None
    db.commit()
    return {"ok": True}
