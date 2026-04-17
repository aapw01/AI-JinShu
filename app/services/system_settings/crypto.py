"""Encryption helpers for system settings secrets."""

from __future__ import annotations

import base64
import hashlib
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


SecurityMode = Literal["encrypted", "plaintext"]


def _build_fernet(master_key: str) -> Fernet:
    # Accept arbitrary string and derive a valid Fernet key.
    """构建fernet。"""
    digest = hashlib.sha256(master_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def get_security_mode() -> SecurityMode:
    """返回安全模式。"""
    return "encrypted" if bool((get_settings().system_settings_master_key or "").strip()) else "plaintext"


def encrypt_api_key(raw_value: str) -> tuple[str, bool]:
    """加密 api key。"""
    value = (raw_value or "").strip()
    if not value:
        return "", False
    master_key = (get_settings().system_settings_master_key or "").strip()
    if not master_key:
        return value, False
    token = _build_fernet(master_key).encrypt(value.encode("utf-8")).decode("utf-8")
    return token, True


def decrypt_api_key(ciphertext: str | None, is_encrypted: bool) -> str:
    """解密 api key。"""
    value = (ciphertext or "").strip()
    if not value:
        return ""
    if not is_encrypted:
        return value
    master_key = (get_settings().system_settings_master_key or "").strip()
    if not master_key:
        return ""
    try:
        return _build_fernet(master_key).decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeDecodeError):
        return ""


def mask_secret(value: str | None) -> str:
    """对 secret 做脱敏处理。"""
    raw = (value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 8:
        return "*" * len(raw)
    return f"{raw[:4]}{'*' * (len(raw) - 8)}{raw[-4:]}"
