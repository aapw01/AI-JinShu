"""Pydantic schemas for novels, chapters, generation."""
from pydantic import BaseModel, ConfigDict, Field


class NovelCreate(BaseModel):
    title: str
    user_id: str | None = None
    target_language: str = "zh"
    native_style_profile: str | None = None
    genre: str | None = None
    style: str | None = None
    pace: str | None = None
    audience: str | None = None
    target_length: str | None = None
    writing_method: str | None = None
    strategy: str | None = None
    user_idea: str | None = None
    inspiration_tags: list[str] | None = None
    config: dict = Field(default_factory=dict)


class NovelUpdate(BaseModel):
    title: str | None = None
    target_language: str | None = None
    native_style_profile: str | None = None
    genre: str | None = None
    style: str | None = None
    pace: str | None = None
    audience: str | None = None
    target_length: str | None = None
    writing_method: str | None = None
    strategy: str | None = None
    user_idea: str | None = None
    inspiration_tags: list[str] | None = None
    config: dict | None = None


class NovelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    target_language: str
    genre: str | None
    style: str | None
    status: str
    created_at: str


class ChapterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: str
    chapter_num: int
    title: str | None
    content: str | None
    summary: str | None
    status: str | None = None
    review_score: float | None
    language_quality_score: float | None
    language_quality_report: str | None = None
    created_at: str


class GenerateRequest(BaseModel):
    num_chapters: int = 1
    start_chapter: int = 1
    require_outline_confirmation: bool = False


class GenerateResponse(BaseModel):
    task_id: str
    novel_id: str
    status: str = "submitted"


class GenerationStatusResponse(BaseModel):
    status: str
    step: str | None = None
    current_phase: str | None = None
    current_chapter: int = 0
    total_chapters: int = 0
    progress: float = 0.0
    token_usage_input: int = 0
    token_usage_output: int = 0
    estimated_cost: float = 0.0
    message: str | None = None
    error: str | None = None
