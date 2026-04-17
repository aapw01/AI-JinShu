"""Email sender service (SendGrid)."""
from __future__ import annotations

import logging
import time

import httpx

from app.core.config import get_settings
from app.core.logging_config import log_event

logger = logging.getLogger(__name__)


def _can_send() -> bool:
    """执行 can send 相关辅助逻辑。"""
    s = get_settings()
    return bool(s.sendgrid_api_key and s.sendgrid_from_email)


def can_send_email() -> bool:
    """Return whether outbound mail is configured and available."""
    return _can_send()


def send_email(to_email: str, subject: str, html_body: str, text_body: str) -> bool:
    """执行 send email 相关辅助逻辑。"""
    s = get_settings()
    if not _can_send():
        log_event(logger, "mail.send.skipped", level=logging.WARNING, reason="sendgrid_not_configured", email=to_email, subject=subject)
        return False
    payload = {
        "personalizations": [{"to": [{"email": to_email}]}],
        "from": {"email": s.sendgrid_from_email},
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text_body},
            {"type": "text/html", "value": html_body},
        ],
    }
    try:
        started = time.perf_counter()
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {s.sendgrid_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        if resp.status_code >= 300:
            log_event(
                logger,
                "mail.send.error",
                level=logging.ERROR,
                provider="sendgrid",
                email=to_email,
                status_code=resp.status_code,
                error_code="MAIL_SEND_FAILED",
                error_category="transient",
                reason=resp.text[:300],
            )
            return False
        log_event(
            logger,
            "mail.send.success",
            provider="sendgrid",
            email=to_email,
            status_code=resp.status_code,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return True
    except Exception as exc:
        log_event(
            logger,
            "mail.send.error",
            level=logging.ERROR,
            provider="sendgrid",
            email=to_email,
            error_code="MAIL_SEND_EXCEPTION",
            error_category="transient",
            error_class=type(exc).__name__,
        )
        return False


def send_verify_email(to_email: str, verify_link: str) -> bool:
    """执行 send verify email 相关辅助逻辑。"""
    subject = "请激活你的 AI 锦书账号"
    text = f"请点击以下链接激活账号：{verify_link}"
    html = (
        "<p>欢迎使用 AI 锦书。</p>"
        f"<p>请点击以下链接激活账号：</p><p><a href=\"{verify_link}\">{verify_link}</a></p>"
    )
    return send_email(to_email, subject, html, text)


def send_reset_password_email(to_email: str, reset_link: str) -> bool:
    """执行 send reset password email 相关辅助逻辑。"""
    subject = "AI 锦书密码重置"
    text = f"请点击以下链接重置密码：{reset_link}"
    html = (
        "<p>你正在重置 AI 锦书账号密码。</p>"
        f"<p><a href=\"{reset_link}\">{reset_link}</a></p>"
    )
    return send_email(to_email, subject, html, text)
