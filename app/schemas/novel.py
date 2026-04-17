"""Pydantic schemas for novels, chapters, generation."""
from pydantic import BaseModel, ConfigDict, Field

from app.schemas.task import TaskErrorDTO, TaskStatusBase


class NovelCreate(BaseModel):
    """小说Create。"""
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
    """小说Update。"""
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
    """小说响应体模型。"""
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
    """创意框架请求体模型。"""
    title: str = Field(min_length=1, max_length=120)
    target_language: str | None = None
    genre: str | None = None
    style: str | None = None
    strategy: str | None = None


class IdeaFrameworkResponse(BaseModel):
    """创意框架响应体模型。"""
    title: str
    one_liner: str
    premise: str
    conflict: str
    hook: str
    selling_point: str
    editable_framework: str
    recommended_genre: str | None = None
    recommended_style: str | None = None


class ChapterResponse(BaseModel):
    """章节响应体模型。"""
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


class ChapterProgressResponse(BaseModel):
    """章节Progress响应体模型。"""
    chapter_num: int
    title: str | None = None
    status: str  # pending | generating | completed | blocked
    volume_no: int
    volume_size: int


class GenerateRequest(BaseModel):
    """Generate请求体模型。"""
    num_chapters: int = 1
    start_chapter: int = 1
    require_outline_confirmation: bool = False
    idempotency_key: str | None = None


class GenerateResponse(BaseModel):
    """Generate响应体模型。"""
    task_id: str
    novel_id: str
    status: str = "submitted"


class RetryGenerationRequest(BaseModel):
    """Retry生成请求体模型。"""
    task_id: str | None = None


class GenerationStatusResponse(TaskStatusBase):
    """生成状态响应体模型。"""
    task_id: str | None = None
    trace_id: str | None = None
    step: str | None = None
    subtask_key: str | None = None
    subtask_label: str | None = None
    subtask_progress: float | None = None
    current_subtask: dict | None = None
    current_chapter: int = 0
    total_chapters: int = 0
    token_usage_input: int = 0
    token_usage_output: int = 0
    estimated_cost: float = 0.0
    volume_no: int | None = None
    volume_size: int | None = None
    pacing_mode: str | None = None
    low_progress_streak: int | None = None
    progress_signal: float | None = None
    decision_state: dict | None = None
    last_error: TaskErrorDTO | None = None


class NovelVersionResponse(BaseModel):
    """小说版本响应体模型。"""
    id: int
    novel_id: str
    version_no: int
    parent_version_id: int | None = None
    status: str
    is_default: bool
    created_at: str
    updated_at: str | None = None


class RewriteAnnotationInput(BaseModel):
    """重写Annotation输入。"""
    chapter_num: int
    start_offset: int | None = None
    end_offset: int | None = None
    selected_text: str | None = None
    issue_type: str = "other"
    instruction: str
    priority: str = "should"
    metadata: dict = Field(default_factory=dict)


class RewriteRequestCreate(BaseModel):
    """重写RequestCreate。"""
    base_version_id: int
    annotations: list[RewriteAnnotationInput]


class RewriteRequestResponse(TaskStatusBase):
    """重写Request响应体模型。"""
    id: int
    novel_id: str
    base_version_id: int
    target_version_id: int
    task_id: str | None = None
    rewrite_from_chapter: int
    rewrite_to_chapter: int
    current_chapter: int | None = None
    created_at: str
    updated_at: str | None = None


class RewriteRetryRequest(BaseModel):
    """重写Retry请求体模型。"""
    request_id: int


class CharacterProfileResponse(BaseModel):
    """角色画像响应体模型。"""
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
