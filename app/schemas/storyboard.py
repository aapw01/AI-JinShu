"""Schemas for storyboard APIs."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


LANES = ("vertical_feed", "horizontal_cinematic")


class StoryboardCreateRequest(BaseModel):
    novel_id: str
    source_novel_version_id: int | None = Field(default=None, ge=1)
    target_episodes: int = Field(default=40, ge=1, le=200)
    target_episode_seconds: int = Field(default=90, ge=30, le=600)
    style_profile: str | None = Field(default=None, max_length=100)
    mode: str = Field(default="quick", pattern="^(quick|professional)$")
    genre_style_key: str | None = Field(default=None, max_length=64)
    director_style_key: str | None = Field(default=None, max_length=64)
    auto_style_recommendation: bool = True
    output_lanes: list[str] = Field(default_factory=lambda: ["vertical_feed", "horizontal_cinematic"])
    professional_mode: bool = True
    audience_goal: str | None = Field(default=None, max_length=100)
    copyright_assertion: bool = True


class StoryboardProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    uuid: str
    novel_id: str
    novel_title: str | None = None
    source_novel_version_id: int | None = None
    status: str
    target_episodes: int
    target_episode_seconds: int
    style_profile: str | None
    professional_mode: bool
    audience_goal: str | None
    mode: str = "quick"
    genre_style_key: str | None = None
    director_style_key: str | None = None
    style_recommendations: list[dict] = Field(default_factory=list)
    output_lanes: list[str]
    active_lane: str
    created_at: str
    updated_at: str | None = None


class StoryboardGenerateResponse(BaseModel):
    task_id: str
    storyboard_project_id: int
    created_version_ids: list[int]


class StoryboardGenerateRequest(BaseModel):
    novel_version_id: int = Field(ge=1)


class StoryboardStyleRecommendationRequest(BaseModel):
    novel_id: str


class StoryboardStyleRecommendationResponse(BaseModel):
    novel_id: str
    recommendations: list[dict]


class StoryboardStylePresetsResponse(BaseModel):
    genre_styles: list[dict]
    director_styles: list[dict]


class StoryboardTaskStatusResponse(BaseModel):
    storyboard_project_id: int
    task_id: str | None = None
    status: str
    run_state: str | None = None
    current_phase: str | None = None
    current_lane: str | None = None
    progress: float = 0.0
    current_episode: int | None = None
    eta_seconds: int | None = None
    eta_label: str | None = None
    message: str | None = None
    error: str | None = None
    error_code: str | None = None
    error_category: str | None = None
    retryable: bool | None = None
    style_consistency_score: float | None = None
    hook_score_episode: dict[str, float] | None = None
    quality_gate_reasons: list[str] | None = None
    character_prompt_phase: str | None = None
    character_profiles_count: int | None = None
    missing_identity_fields_count: int | None = None
    failed_identity_characters: list[dict] | None = None


class StoryboardVersionResponse(BaseModel):
    id: int
    storyboard_project_id: int
    source_novel_version_id: int | None = None
    version_no: int
    parent_version_id: int | None = None
    lane: str
    status: str
    is_default: bool
    is_final: bool
    quality_report_json: dict
    created_at: str
    updated_at: str | None = None


class StoryboardShotResponse(BaseModel):
    id: int
    storyboard_version_id: int
    episode_no: int
    scene_no: int
    shot_no: int
    location: str | None = None
    time_of_day: str | None = None
    shot_size: str | None = None
    camera_angle: str | None = None
    camera_move: str | None = None
    duration_sec: int
    characters_json: list[str]
    action: str | None = None
    dialogue: str | None = None
    emotion_beat: str | None = None
    transition: str | None = None
    sound_hint: str | None = None
    production_note: str | None = None
    blocking: str | None = None
    motivation: str | None = None
    performance_note: str | None = None
    continuity_anchor: str | None = None
    created_at: str
    updated_at: str | None = None


class StoryboardShotUpdateRequest(BaseModel):
    location: str | None = None
    time_of_day: str | None = None
    shot_size: str | None = None
    camera_angle: str | None = None
    camera_move: str | None = None
    duration_sec: int | None = Field(default=None, ge=1, le=30)
    characters_json: list[str] | None = None
    action: str | None = None
    dialogue: str | None = None
    emotion_beat: str | None = None
    transition: str | None = None
    sound_hint: str | None = None
    production_note: str | None = None
    blocking: str | None = None
    motivation: str | None = None
    performance_note: str | None = None
    continuity_anchor: str | None = None


class StoryboardActionResponse(BaseModel):
    ok: bool
    storyboard_project_id: int
    task_id: str | None = None
    run_state: str | None = None


class StoryboardOptimizeResponse(BaseModel):
    ok: bool
    storyboard_project_id: int
    version_id: int
    optimized_shots: int
    quality_report_json: dict


class StoryboardDiffResponse(BaseModel):
    storyboard_project_id: int
    version_id: int
    compare_to: int
    summary: dict
    episodes: list[dict]


class StoryboardCharacterPromptResponse(BaseModel):
    id: int
    storyboard_project_id: int
    storyboard_version_id: int
    lane: str
    character_key: str
    display_name: str
    skin_tone: str
    ethnicity: str
    master_prompt_text: str
    negative_prompt_text: str | None = None
    style_tags_json: list[str] = Field(default_factory=list)
    consistency_anchors_json: list[str] = Field(default_factory=list)
    quality_score: float | None = None
    created_at: str
    updated_at: str | None = None


class StoryboardCharacterGenerateResponse(BaseModel):
    ok: bool
    storyboard_project_id: int
    storyboard_version_id: int
    lane: str
    generated_count: int
    profiles_count: int
    missing_identity_fields_count: int
    failed_identity_characters: list[dict] = Field(default_factory=list)


class StoryboardPreflightRequest(BaseModel):
    force_refresh_snapshot: bool = False


class StoryboardPreflightResponse(BaseModel):
    ok: bool
    storyboard_project_id: int
    gate_status: str
    source_novel_version_id: int
    profiles_count: int
    chapters_count: int
    missing_identity_fields_count: int
    failed_identity_characters: list[dict] = Field(default_factory=list)
    snapshot_hash: str


class StoryboardRunLaneResponse(BaseModel):
    id: int
    lane: str
    storyboard_version_id: int
    creation_task_public_id: str | None = None
    status: str
    run_state: str
    current_phase: str | None = None
    progress: float = 0.0
    message: str | None = None
    error: str | None = None
    error_code: str | None = None
    error_category: str | None = None
    gate_report_json: dict = Field(default_factory=dict)
    updated_at: str | None = None


class StoryboardRunResponse(BaseModel):
    id: int
    public_id: str
    storyboard_project_id: int
    status: str
    run_state: str
    current_phase: str | None = None
    progress: float = 0.0
    message: str | None = None
    error: str | None = None
    error_code: str | None = None
    error_category: str | None = None
    lanes: list[StoryboardRunLaneResponse] = Field(default_factory=list)
    created_at: str
    updated_at: str | None = None
    finished_at: str | None = None


class StoryboardRunActionRequest(BaseModel):
    action: str = Field(pattern="^(pause|resume|cancel|retry)$")


class StoryboardRunActionResponse(BaseModel):
    ok: bool
    storyboard_project_id: int
    run_id: str
    action: str
    run_state: str
    status: str


class StoryboardCharacterCardResponse(BaseModel):
    id: int
    storyboard_project_id: int
    storyboard_version_id: int
    lane: str
    character_key: str
    display_name: str
    skin_tone: str
    ethnicity: str
    master_prompt_text: str
    negative_prompt_text: str | None = None
    style_tags_json: list[str] = Field(default_factory=list)
    consistency_anchors_json: list[str] = Field(default_factory=list)
    quality_score: float | None = None
    metadata_json: dict = Field(default_factory=dict)
    created_at: str
    updated_at: str | None = None


class StoryboardCharacterCardUpdateRequest(BaseModel):
    skin_tone: str | None = None
    ethnicity: str | None = None
    master_prompt_text: str | None = None
    negative_prompt_text: str | None = None
    consistency_anchors_json: list[str] | None = None
    metadata_json: dict | None = None


class StoryboardExportCreateRequest(BaseModel):
    format: str = Field(pattern="^(csv|json|pdf)$")


class StoryboardExportCreateResponse(BaseModel):
    ok: bool
    storyboard_project_id: int
    version_id: int
    export_id: str
    status: str


class StoryboardExportStatusResponse(BaseModel):
    id: str
    storyboard_project_id: int
    storyboard_version_id: int
    format: str
    status: str
    file_name: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    error: str | None = None
    error_code: str | None = None
    download_url: str | None = None
    created_at: str
    updated_at: str | None = None
