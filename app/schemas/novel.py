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
    updated_at: str | None = None


class IdeaFrameworkRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    target_language: str | None = None
    genre: str | None = None
    style: str | None = None
    strategy: str | None = None


class IdeaFrameworkResponse(BaseModel):
    title: str
    one_liner: str
    premise: str
    conflict: str
    hook: str
    selling_point: str
    editable_framework: str


class ChapterResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    novel_id: str
    version_id: int
    chapter_num: int
    title: str | None
    content: str | None
    summary: str | None
    status: str | None = None
    review_score: float | None
    language_quality_score: float | None
    language_quality_report: str | None = None
    word_count: int = 0
    created_at: str


class GenerateRequest(BaseModel):
    num_chapters: int = 1
    start_chapter: int = 1
    require_outline_confirmation: bool = False
    idempotency_key: str | None = None


class GenerateResponse(BaseModel):
    task_id: str
    novel_id: str
    status: str = "submitted"


class RetryGenerationRequest(BaseModel):
    task_id: str | None = None


class GenerationStatusResponse(BaseModel):
    task_id: str | None = None
    status: str
    trace_id: str | None = None
    run_state: str | None = None
    step: str | None = None
    current_phase: str | None = None
    subtask_key: str | None = None
    subtask_label: str | None = None
    subtask_progress: float | None = None
    current_subtask: dict | None = None
    current_chapter: int = 0
    total_chapters: int = 0
    progress: float = 0.0
    token_usage_input: int = 0
    token_usage_output: int = 0
    estimated_cost: float = 0.0
    volume_no: int | None = None
    volume_size: int | None = None
    pacing_mode: str | None = None
    low_progress_streak: int | None = None
    progress_signal: float | None = None
    decision_state: dict | None = None
    eta_seconds: int | None = None
    eta_label: str | None = None
    message: str | None = None
    error: str | None = None
    error_code: str | None = None
    error_category: str | None = None
    retryable: bool | None = None
    last_error: dict | None = None


class NovelVersionResponse(BaseModel):
    id: int
    novel_id: str
    version_no: int
    parent_version_id: int | None = None
    status: str
    is_default: bool
    created_at: str
    updated_at: str | None = None


class RewriteAnnotationInput(BaseModel):
    chapter_num: int
    start_offset: int | None = None
    end_offset: int | None = None
    selected_text: str | None = None
    issue_type: str = "other"
    instruction: str
    priority: str = "should"
    metadata: dict = Field(default_factory=dict)


class RewriteRequestCreate(BaseModel):
    base_version_id: int
    annotations: list[RewriteAnnotationInput]


class RewriteRequestResponse(BaseModel):
    id: int
    novel_id: str
    base_version_id: int
    target_version_id: int
    task_id: str | None = None
    status: str
    rewrite_from_chapter: int
    rewrite_to_chapter: int
    current_chapter: int | None = None
    progress: float = 0.0
    eta_seconds: int | None = None
    eta_label: str | None = None
    message: str | None = None
    error: str | None = None
    error_code: str | None = None
    error_category: str | None = None
    retryable: bool | None = None
    created_at: str
    updated_at: str | None = None


class RewriteRetryRequest(BaseModel):
    request_id: int


class CharacterProfileResponse(BaseModel):
    id: int
    novel_id: str
    character_key: str
    display_name: str
    gender_presentation: str | None = None
    age_band: str | None = None
    skin_tone: str | None = None
    ethnicity: str | None = None
    body_type: str | None = None
    face_features: str | None = None
    hair_style: str | None = None
    hair_color: str | None = None
    eye_color: str | None = None
    wardrobe_base_style: str | None = None
    signature_items_json: list[str] = Field(default_factory=list)
    visual_do_not_change_json: list[str] = Field(default_factory=list)
    evidence_json: list[dict] = Field(default_factory=list)
    confidence: float = 0.0
    updated_chapter_num: int | None = None
    created_at: str
    updated_at: str | None = None
