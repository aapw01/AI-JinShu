"""AuthN/AuthZ HTTP errors."""
from fastapi import HTTPException


def unauthorized(message: str = "Unauthorized") -> HTTPException:
    return HTTPException(status_code=401, detail=message)


def forbidden(message: str = "Forbidden") -> HTTPException:
    return HTTPException(status_code=403, detail=message)
