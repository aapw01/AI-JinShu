"""AuthN/AuthZ HTTP errors."""
from fastapi import HTTPException

from app.core.api_errors import error_detail


def unauthorized(message: str = "Unauthorized") -> HTTPException:
    return HTTPException(status_code=401, detail=error_detail("unauthorized", message))


def forbidden(message: str = "Forbidden") -> HTTPException:
    return HTTPException(status_code=403, detail=error_detail("forbidden", message))
