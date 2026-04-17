"""Security helpers for password hashing and one-time tokens."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings

_PASSWORD_RE = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^A-Za-z\d]).{10,128}$"
)
_COMMON_WEAK = {
    "password",
    "password123",
    "qwerty123",
    "12345678",
    "11111111",
    "admin123",
}


def utc_now() -> datetime:
    """执行 utc now 相关辅助逻辑。"""
    return datetime.now(timezone.utc)


def validate_password_complexity(password: str) -> tuple[bool, str]:
    """校验密码complexity。"""
    pwd = (password or "").strip()
    if not _PASSWORD_RE.match(pwd):
        return (
            False,
            "密码至少10位，且必须包含大写字母、小写字母、数字、特殊字符。",
        )
    lowered = pwd.lower()
    if lowered in _COMMON_WEAK:
        return False, "密码过于常见，请更换更复杂的密码。"
    return True, ""


def hash_password(password: str) -> str:
    """计算密码哈希。"""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        200_000,
    )
    return f"pbkdf2_sha256$200000${salt}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """执行 verify password 相关辅助逻辑。"""
    try:
        algo, rounds_s, salt, expected = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
    except Exception:
        return False
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        rounds,
    ).hex()
    return hmac.compare_digest(digest, expected)


def new_raw_token() -> str:
    """执行 new raw token 相关辅助逻辑。"""
    return secrets.token_urlsafe(32)


def token_hash(raw_token: str) -> str:
    """执行 token hash 相关辅助逻辑。"""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def future_minutes(minutes: int) -> datetime:
    """执行 future minutes 相关辅助逻辑。"""
    return utc_now() + timedelta(minutes=max(1, int(minutes)))


def build_verify_link(raw_token: str) -> str:
    """构建验证链接。"""
    settings = get_settings()
    base = settings.auth_frontend_base_url.rstrip("/")
    return f"{base}/auth/verify?token={raw_token}"


def build_reset_link(raw_token: str) -> str:
    """构建重置链接。"""
    settings = get_settings()
    base = settings.auth_frontend_base_url.rstrip("/")
    return f"{base}/auth/forgot-password?token={raw_token}"
