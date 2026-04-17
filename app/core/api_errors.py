"""Unified API error payload helpers."""
from __future__ import annotations

from fastapi import HTTPException
from fastapi.responses import JSONResponse


ErrorMeta = dict[str, str | int | float | bool | None | list | dict]


def error_detail(
    error_code: str,
    message: str,
    *,
    meta: ErrorMeta | None = None,
    retryable: bool | None = None,
) -> dict:
    """执行 error detail 相关辅助逻辑。"""
    payload: dict[str, object] = {
        "error_code": str(error_code),
        "message": str(message),
    }
    if meta is not None:
        payload["meta"] = meta
    if retryable is not None:
        payload["retryable"] = bool(retryable)
    return payload


def http_error(
    status_code: int,
    error_code: str,
    message: str,
    *,
    meta: ErrorMeta | None = None,
    retryable: bool | None = None,
) -> HTTPException:
    """执行 http error 相关辅助逻辑。"""
    return HTTPException(
        status_code=status_code,
        detail=error_detail(error_code, message, meta=meta, retryable=retryable),
    )


def error_response(
    status_code: int,
    error_code: str,
    message: str,
    *,
    meta: ErrorMeta | None = None,
    retryable: bool | None = None,
) -> JSONResponse:
    """执行 error response 相关辅助逻辑。"""
    return JSONResponse(
        status_code=status_code,
        content={"detail": error_detail(error_code, message, meta=meta, retryable=retryable)},
    )
