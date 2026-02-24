"""Pydantic schemas for API."""
from app.schemas.novel import (
    NovelCreate,
    NovelUpdate,
    NovelResponse,
    ChapterResponse,
    GenerateRequest,
    GenerateResponse,
    GenerationStatusResponse,
)

__all__ = [
    "NovelCreate",
    "NovelUpdate",
    "NovelResponse",
    "ChapterResponse",
    "GenerateRequest",
    "GenerateResponse",
    "GenerationStatusResponse",
]
