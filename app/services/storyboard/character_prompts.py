"""Storyboard character master prompt generation from incremental character profiles."""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.llm import get_llm_with_fallback
from app.core.strategy import get_model_for_stage
from app.models.novel import Novel, StoryCharacterProfile
from app.models.storyboard import StoryboardCharacterPrompt, StoryboardProject, StoryboardShot, StoryboardVersion
from app.prompts import render_prompt
from app.services.generation.character_profiles import ETHNICITIES, SKIN_TONES

logger = logging.getLogger(__name__)


def _key_from_name(name: str) -> str:
    raw = (name or "").strip().lower()
    return re.sub(r"[^a-z0-9\u4e00-\u9fa5]+", "-", raw).strip("-")[:120]


def _compact_shot_context(shots: list[StoryboardShot], character_name: str) -> str:
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
    miss: list[str] = []
    if str(profile.skin_tone or "").strip() not in SKIN_TONES:
        miss.append("skin_tone")
    if str(profile.ethnicity or "").strip() not in ETHNICITIES:
        miss.append("ethnicity")
    return miss


def _compose_master_prompt(
    *,
    profile: StoryCharacterProfile,
    lane: str,
    style_profile: str,
    genre: str,
    shot_context: str,
    strategy: str | None,
) -> str:
    provider, model = get_model_for_stage(strategy or "web-novel", "architect")
    llm = get_llm_with_fallback(provider, model)
    prompt = render_prompt(
        "storyboard_character_master_prompt",
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
    try:
        resp = llm.invoke(prompt)
        text = str(getattr(resp, "content", "") or "").strip()
        return text[:1400] if text else prompt[:1200]
    except Exception as exc:
        logger.warning("character master prompt compose fallback name=%s err=%s", profile.display_name, exc)
        return prompt[:1200]


def compose_character_prompts_for_version(
    *,
    db: Session,
    project: StoryboardProject,
    version: StoryboardVersion,
    novel: Novel,
    force_regenerate: bool = False,
) -> dict[str, Any]:
    profiles = db.execute(
        select(StoryCharacterProfile)
        .where(StoryCharacterProfile.novel_id == novel.id)
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
        missing = _identity_missing(profile)
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
        master = _compose_master_prompt(
            profile=profile,
            lane=version.lane,
            style_profile=project.style_profile or "通用影视",
            genre=novel.genre or "通用",
            shot_context=shot_context,
            strategy=novel.strategy,
        )
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
                style_tags_json=[project.style_profile or "", version.lane, novel.genre or ""],
                consistency_anchors_json=(profile.visual_do_not_change_json or [])[:10],
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
