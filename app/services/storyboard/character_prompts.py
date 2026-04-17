"""Storyboard character master prompt generation from incremental character profiles."""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.llm import get_llm_with_fallback
from app.core.strategy import get_model_for_stage
from app.models.novel import Novel, StoryCharacterProfile
from app.models.storyboard import StoryboardCharacterPrompt, StoryboardProject, StoryboardShot, StoryboardVersion
from app.prompts import render_prompt
from app.services.generation.character_profiles import (
    ETHNICITIES,
    SKIN_TONES,
    normalize_ethnicity,
    normalize_skin_tone,
)

logger = logging.getLogger(__name__)


def _key_from_name(name: str) -> str:
    """执行 key from name 相关辅助逻辑。"""
    raw = (name or "").strip().lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fa5]+", "-", raw).strip("-")[:120]


class CharacterPromptSchema(BaseModel):
    """角色提示词结构化模型。"""
    model_config = ConfigDict(extra="forbid")
    master_prompt_text: str = Field(min_length=1)
    identity_lock: list[str] = Field(default_factory=list)
    negative_prompt_text: str = Field(min_length=1)
    camera_safe_notes: list[str] = Field(default_factory=list)

def _compact_shot_context(shots: list[StoryboardShot], character_name: str) -> str:
    """执行 compact shot context 相关辅助逻辑。"""
    rows: list[str] = []
    for s in shots:
        in_cast = character_name in (s.characters_json or [])
        in_text = character_name in (s.action or "") or character_name in (s.dialogue or "")
        if not (in_cast or in_text):
            continue
        rows.append(
            f"E{s.episode_no}-S{s.scene_no}-#{s.shot_no}: "
            f"{(s.action or '')[:80]} | {(s.dialogue or '')[:60]}"
        )
        if len(rows) >= 8:
            break
    return "\n".join(rows) if rows else "无"


def _identity_missing(profile: StoryCharacterProfile) -> list[str]:
    """执行 identity missing 相关辅助逻辑。"""
    miss: list[str] = []
    if str(profile.skin_tone or "").strip() not in SKIN_TONES:
        miss.append("skin_tone")
    if str(profile.ethnicity or "").strip() not in ETHNICITIES:
        miss.append("ethnicity")
    return miss


def _default_ethnicity_from_name(name: str) -> str:
    """执行 default ethnicity from name 相关辅助逻辑。"""
    return "east_asian" if re.search(r"[\u4e00-\u9fff]", str(name or "")) else "other_specified"


def _ensure_identity_fields(profile: StoryCharacterProfile) -> list[str]:
    """确保identity字段存在并可用。"""
    skin = normalize_skin_tone(profile.skin_tone) or "medium"
    ethnicity = normalize_ethnicity(profile.ethnicity) or _default_ethnicity_from_name(profile.display_name)

    changed = False
    if profile.skin_tone != skin:
        profile.skin_tone = skin
        changed = True
    if profile.ethnicity != ethnicity:
        profile.ethnicity = ethnicity
        changed = True
    if changed:
        metadata = profile.metadata_ if isinstance(profile.metadata_, dict) else {}
        metadata = {
            **metadata,
            "identity_autofill": {
                "source": "storyboard_gate_fallback",
                "skin_tone": skin,
                "ethnicity": ethnicity,
            },
        }
        profile.metadata_ = metadata
    return _identity_missing(profile)


def _compose_master_prompt(
    *,
    profile: StoryCharacterProfile,
    lane: str,
    style_profile: str,
    genre: str,
    shot_context: str,
    strategy: str | None,
) -> dict[str, Any]:
    """组装master提示词。"""
    provider, model = get_model_for_stage(strategy or "web-novel", "architect")
    llm = get_llm_with_fallback(provider, model)
    template = "storyboard_character_master_prompt"
    prompt = render_prompt(
        template,
        character_json=json.dumps(
            {
                "display_name": profile.display_name,
                "gender_presentation": profile.gender_presentation,
                "age_band": profile.age_band,
                "skin_tone": profile.skin_tone,
                "ethnicity": profile.ethnicity,
                "body_type": profile.body_type,
                "face_features": profile.face_features,
                "hair_style": profile.hair_style,
                "hair_color": profile.hair_color,
                "eye_color": profile.eye_color,
                "wardrobe_base_style": profile.wardrobe_base_style,
                "signature_items": profile.signature_items_json or [],
                "visual_do_not_change": profile.visual_do_not_change_json or [],
            },
            ensure_ascii=False,
        ),
        lane=lane,
        style_profile=style_profile,
        genre=genre,
        shot_context=shot_context,
    )
    default_negative = render_prompt(
        "storyboard_character_negative_prompt",
        lane=lane,
        style_profile=style_profile,
    ).strip()

    def _fallback_payload(reason: str) -> dict[str, Any]:
        """当结构化角色提示词生成失败时，退化为一份可直接使用的保守提示词。"""
        fallback_master = (
            f"{profile.display_name}，{lane}镜头语言，{genre}题材，{style_profile}风格；"
            f"肤色={profile.skin_tone}，族裔={profile.ethnicity}，"
            f"发型={profile.hair_style or '自然'}，发色={profile.hair_color or '自然黑'}，"
            f"服装基调={profile.wardrobe_base_style or '贴合职业身份'}。"
        )
        return {
            "master_prompt_text": fallback_master[:1400],
            "identity_lock": [
                f"肤色固定：{profile.skin_tone}",
                f"族裔固定：{profile.ethnicity}",
                f"发型固定：{profile.hair_style or '自然短发'}",
            ],
            "negative_prompt_text": default_negative or "避免脸型漂移、发色漂移、时代错置、服装突变",
            "camera_safe_notes": ["保持脸部轮廓一致", "镜头切换不得改变发色与服装主廓形"],
            "metadata": {
                "prompt_template": template,
                "prompt_version": "v2",
                "fallback_reason": reason,
            },
        }
    try:
        last_exc: Exception | None = None
        for method in ("json_schema", "function_calling", "json_mode"):
            try:
                kwargs: dict[str, Any] = {"method": method, "include_raw": True}
                structured = llm.with_structured_output(CharacterPromptSchema, **kwargs)
                payload = structured.invoke(prompt)
                if isinstance(payload, dict) and any(k in payload for k in ("parsed", "raw", "parsing_error")):
                    if payload.get("parsing_error") is not None:
                        raise RuntimeError(str(payload.get("parsing_error")))
                    parsed = payload.get("parsed")
                else:
                    parsed = payload
                if isinstance(parsed, BaseModel):
                    parsed_data = parsed.model_dump()
                elif isinstance(parsed, dict):
                    parsed_data = parsed
                else:
                    raise RuntimeError("invalid structured payload")
                validated = CharacterPromptSchema.model_validate(parsed_data)
                return {
                    "master_prompt_text": validated.master_prompt_text[:1400],
                    "identity_lock": [str(x)[:120] for x in (validated.identity_lock or []) if str(x).strip()][:8],
                    "negative_prompt_text": validated.negative_prompt_text[:800],
                    "camera_safe_notes": [str(x)[:120] for x in (validated.camera_safe_notes or []) if str(x).strip()][:8],
                    "metadata": {
                        "prompt_template": template,
                        "prompt_version": "v2",
                        "method": method,
                        "source": "llm_structured",
                    },
                }
            except Exception as exc:  # pragma: no cover - multi-provider/model behavior
                last_exc = exc
                continue
        if last_exc:
            logger.warning(
                "character master prompt structured fallback name=%s err=%s",
                profile.display_name,
                last_exc,
            )
        return _fallback_payload("structured_contract_failed")
    except ValidationError as exc:
        logger.warning("character master prompt validation fallback name=%s err=%s", profile.display_name, exc)
        return _fallback_payload("validation_failed")
    except Exception as exc:
        logger.warning("character master prompt compose fallback name=%s err=%s", profile.display_name, exc)
        return _fallback_payload(type(exc).__name__)


def compose_character_prompts_for_version(
    *,
    db: Session,
    project: StoryboardProject,
    version: StoryboardVersion,
    novel: Novel,
    force_regenerate: bool = False,
) -> dict[str, Any]:
    """组装角色提示词for版本。"""
    profiles = db.execute(
        select(StoryCharacterProfile)
        .where(
            StoryCharacterProfile.novel_id == novel.id,
            StoryCharacterProfile.novel_version_id == version.source_novel_version_id,
        )
        .order_by(StoryCharacterProfile.updated_chapter_num.desc().nullslast(), StoryCharacterProfile.id.asc())
    ).scalars().all()
    shots = db.execute(
        select(StoryboardShot)
        .where(StoryboardShot.storyboard_version_id == version.id)
        .order_by(StoryboardShot.episode_no.asc(), StoryboardShot.scene_no.asc(), StoryboardShot.shot_no.asc())
    ).scalars().all()

    if not profiles:
        return {
            "profiles_count": 0,
            "generated_count": 0,
            "missing_identity_fields_count": 1,
            "failed_identity_characters": [
                {
                    "character_key": "none",
                    "display_name": "无角色画像",
                    "missing_fields": ["skin_tone", "ethnicity"],
                    "reason": "no_character_profiles",
                }
            ],
        }

    if force_regenerate:
        db.execute(delete(StoryboardCharacterPrompt).where(StoryboardCharacterPrompt.storyboard_version_id == version.id))
        db.flush()
    else:
        existing_count = db.execute(
            select(StoryboardCharacterPrompt).where(StoryboardCharacterPrompt.storyboard_version_id == version.id)
        ).scalars().all()
        if existing_count:
            return {
                "profiles_count": len(profiles),
                "generated_count": len(existing_count),
                "missing_identity_fields_count": 0,
                "failed_identity_characters": [],
            }

    missing_rows: list[dict[str, Any]] = []
    created = 0
    for profile in profiles:
        missing = _ensure_identity_fields(profile)
        if missing:
            missing_rows.append(
                {
                    "character_key": profile.character_key,
                    "display_name": profile.display_name,
                    "missing_fields": missing,
                }
            )
            continue
        shot_context = _compact_shot_context(shots, profile.display_name)
        prompt_payload = _compose_master_prompt(
            profile=profile,
            lane=version.lane,
            style_profile=project.style_profile or "通用影视",
            genre=novel.genre or "通用",
            shot_context=shot_context,
            strategy=novel.strategy,
        )
        master = str(prompt_payload.get("master_prompt_text") or "").strip()
        identity_lock = [str(x) for x in (prompt_payload.get("identity_lock") or []) if str(x).strip()]
        negative = str(prompt_payload.get("negative_prompt_text") or "").strip()
        camera_safe_notes = [str(x) for x in (prompt_payload.get("camera_safe_notes") or []) if str(x).strip()]
        if not negative:
            negative = render_prompt(
                "storyboard_character_negative_prompt",
                lane=version.lane,
                style_profile=project.style_profile or "通用影视",
            ).strip()
        db.add(
            StoryboardCharacterPrompt(
                storyboard_project_id=project.id,
                storyboard_version_id=version.id,
                lane=version.lane,
                character_key=profile.character_key or _key_from_name(profile.display_name),
                display_name=profile.display_name,
                skin_tone=profile.skin_tone or "",
                ethnicity=profile.ethnicity or "",
                master_prompt_text=master,
                negative_prompt_text=negative,
                style_tags_json=[project.style_profile or "", version.lane, novel.genre or ""] + camera_safe_notes[:2],
                consistency_anchors_json=((profile.visual_do_not_change_json or []) + identity_lock)[:10],
                quality_score=profile.confidence or 0.0,
            )
        )
        created += 1
    db.flush()
    return {
        "profiles_count": len(profiles),
        "generated_count": created,
        "missing_identity_fields_count": len(missing_rows),
        "failed_identity_characters": missing_rows[:20],
    }


def list_character_prompts(
    db: Session,
    *,
    project_id: int,
    version_id: int,
    lane: str | None = None,
) -> list[StoryboardCharacterPrompt]:
    """列出角色提示词。"""
    stmt = (
        select(StoryboardCharacterPrompt)
        .where(
            StoryboardCharacterPrompt.storyboard_project_id == project_id,
            StoryboardCharacterPrompt.storyboard_version_id == version_id,
        )
        .order_by(StoryboardCharacterPrompt.display_name.asc())
    )
    if lane:
        stmt = stmt.where(StoryboardCharacterPrompt.lane == lane)
    return db.execute(stmt).scalars().all()


def export_character_prompts_csv(rows: list[StoryboardCharacterPrompt]) -> str:
    """执行 export character prompts csv 相关辅助逻辑。"""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "character_key",
            "display_name",
            "lane",
            "skin_tone",
            "ethnicity",
            "master_prompt_text",
            "negative_prompt_text",
            "style_tags",
            "consistency_anchors",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.character_key,
                r.display_name,
                r.lane,
                r.skin_tone,
                r.ethnicity,
                r.master_prompt_text,
                r.negative_prompt_text or "",
                " | ".join([str(x) for x in (r.style_tags_json or []) if str(x).strip()]),
                " | ".join([str(x) for x in (r.consistency_anchors_json or []) if str(x).strip()]),
            ]
        )
    return buf.getvalue()
