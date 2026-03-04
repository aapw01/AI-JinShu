"""Incremental hard-identity character profile extraction during generation."""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.llm import get_llm_with_fallback
from app.core.strategy import get_model_for_stage
from app.models.novel import StoryCharacterProfile
from app.prompts import render_prompt

logger = logging.getLogger(__name__)

SKIN_TONES = {
    "very_fair",
    "fair",
    "light",
    "medium",
    "tan",
    "deep",
    "very_deep",
}

ETHNICITIES = {
    "east_asian",
    "southeast_asian",
    "south_asian",
    "middle_eastern",
    "black_african",
    "black_diaspora",
    "white_european",
    "latino_hispanic",
    "indigenous",
    "mixed",
    "other_specified",
}

SKIN_TONE_ALIASES: dict[str, str] = {
    "pale": "very_fair",
    "porcelain": "very_fair",
    "white": "fair",
    "fair_skin": "fair",
    "light_skin": "light",
    "wheat": "tan",
    "wheatish": "tan",
    "dark": "deep",
    "dark_skin": "deep",
    "very_dark": "very_deep",
    "白皙": "very_fair",
    "冷白皮": "very_fair",
    "偏白": "fair",
    "白皮": "fair",
    "浅肤色": "light",
    "黄皮": "medium",
    "自然肤色": "medium",
    "小麦色": "tan",
    "古铜": "tan",
    "深肤色": "deep",
    "黑皮": "deep",
    "深棕": "very_deep",
}

ETHNICITY_ALIASES: dict[str, str] = {
    "chinese": "east_asian",
    "han": "east_asian",
    "east_asian_chinese": "east_asian",
    "asian": "east_asian",
    "japanese": "east_asian",
    "korean": "east_asian",
    "thai": "southeast_asian",
    "vietnamese": "southeast_asian",
    "filipino": "southeast_asian",
    "indian": "south_asian",
    "pakistani": "south_asian",
    "arab": "middle_eastern",
    "middle_east": "middle_eastern",
    "african": "black_african",
    "black": "black_diaspora",
    "european": "white_european",
    "white": "white_european",
    "latino": "latino_hispanic",
    "hispanic": "latino_hispanic",
    "indigenous_people": "indigenous",
    "mixed_race": "mixed",
    "other": "other_specified",
    "中国人": "east_asian",
    "汉族": "east_asian",
    "东亚": "east_asian",
    "东南亚": "southeast_asian",
    "南亚": "south_asian",
    "中东": "middle_eastern",
    "非洲": "black_african",
    "欧美": "white_european",
    "拉丁裔": "latino_hispanic",
    "混血": "mixed",
    "其他": "other_specified",
}

_PAREN_HINT_RE = re.compile(r"[（(][^）)]*[）)]")


def normalize_character_key(name: str) -> str:
    raw = (name or "").strip().lower()
    if not raw:
        return ""
    return re.sub(r"[^a-z0-9\u4e00-\u9fa5]+", "-", raw).strip("-")[:120]


def _safe_json_loads(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    if not raw.startswith("{"):
        m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if m:
            raw = m.group(0)
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _normalize_enum_token(value: str | None) -> str:
    return re.sub(r"[\s\-]+", "_", str(value or "").strip().lower())


def _clean_enum(value: str | None, allowed: set[str], aliases: dict[str, str] | None = None) -> str | None:
    text = _normalize_enum_token(value)
    if not text:
        return None
    if text in allowed:
        return text
    if aliases and text in aliases:
        mapped = aliases[text]
        return mapped if mapped in allowed else None
    return None


def normalize_skin_tone(value: str | None) -> str | None:
    return _clean_enum(value, SKIN_TONES, SKIN_TONE_ALIASES)


def normalize_ethnicity(value: str | None) -> str | None:
    return _clean_enum(value, ETHNICITIES, ETHNICITY_ALIASES)


def _name_candidates(name: str) -> list[str]:
    raw = str(name or "").strip()
    if not raw:
        return []
    out = [raw]
    stripped = _PAREN_HINT_RE.sub("", raw).strip()
    if stripped and stripped not in out:
        out.append(stripped)
    return out


def _name_norm(name: str) -> str:
    base = _PAREN_HINT_RE.sub("", str(name or "")).strip().lower()
    return re.sub(r"\s+", "", base)


def list_character_profiles(db: Session, novel_id: int, novel_version_id: int | None = None) -> list[StoryCharacterProfile]:
    stmt = select(StoryCharacterProfile).where(StoryCharacterProfile.novel_id == novel_id)
    if novel_version_id is not None:
        stmt = stmt.where(StoryCharacterProfile.novel_version_id == novel_version_id)
    stmt = stmt.order_by(StoryCharacterProfile.updated_chapter_num.desc().nullslast(), StoryCharacterProfile.id.asc())
    return db.execute(stmt).scalars().all()


def _upsert_stub_profiles_from_prewrite(
    db: Session,
    novel_id: int,
    novel_version_id: int | None,
    prewrite: dict,
) -> None:
    chars = ((prewrite or {}).get("specification") or {}).get("characters") or []
    if not isinstance(chars, list):
        return
    stmt_keys = select(StoryCharacterProfile.character_key).where(StoryCharacterProfile.novel_id == novel_id)
    if novel_version_id is not None:
        stmt_keys = stmt_keys.where(StoryCharacterProfile.novel_version_id == novel_version_id)
    existing_keys = {str(x).strip() for x in db.execute(stmt_keys).scalars().all() if str(x).strip()}
    for c in chars[:120]:
        if not isinstance(c, dict):
            continue
        display_name = str(c.get("name") or "").strip()
        if not display_name:
            continue
        key = normalize_character_key(display_name)
        if not key:
            continue
        if key in existing_keys:
            continue
        try:
            with db.begin_nested():
                db.add(
                    StoryCharacterProfile(
                        novel_id=novel_id,
                        novel_version_id=novel_version_id,
                        character_key=key,
                        display_name=display_name,
                        confidence=0.0,
                        evidence_json=[],
                        metadata_={"source": "prewrite_stub"},
                    )
                )
                db.flush()
        except IntegrityError:
            # Another concurrent worker inserted the same (novel_id, character_key).
            pass
        existing_keys.add(key)


def _should_process_character(name: str, content: str, extracted_facts: dict[str, Any]) -> bool:
    names = _name_candidates(name)
    if not names:
        return False
    source_text = content or ""
    if any(n in source_text for n in names):
        return True
    name_norms = {_name_norm(n) for n in names if _name_norm(n)}
    entities = extracted_facts.get("entities") if isinstance(extracted_facts, dict) else []
    for ent in entities or []:
        if not isinstance(ent, dict):
            continue
        entity_name = str(ent.get("name") or "").strip()
        if not entity_name:
            continue
        entity_norm = _name_norm(entity_name)
        if entity_norm and any(entity_norm == n or entity_norm in n or n in entity_norm for n in name_norms):
            return True
    return False


def _merge_profile(existing: StoryCharacterProfile, candidate: dict[str, Any], chapter_num: int) -> None:
    confidence = float(candidate.get("confidence", 0.0) or 0.0)
    existing_conf = float(existing.confidence or 0.0)
    replace = confidence >= existing_conf

    def assign(attr: str, val: Any, *, allow_empty: bool = False):
        if val is None:
            return
        text = str(val).strip() if isinstance(val, str) else val
        if isinstance(text, str) and not text and not allow_empty:
            return
        if replace or not getattr(existing, attr):
            setattr(existing, attr, text)

    assign("gender_presentation", candidate.get("gender_presentation"))
    assign("age_band", candidate.get("age_band"))
    assign("skin_tone", normalize_skin_tone(candidate.get("skin_tone")))
    assign("ethnicity", normalize_ethnicity(candidate.get("ethnicity")))
    assign("body_type", candidate.get("body_type"))
    assign("face_features", candidate.get("face_features"))
    assign("hair_style", candidate.get("hair_style"))
    assign("hair_color", candidate.get("hair_color"))
    assign("eye_color", candidate.get("eye_color"))
    assign("wardrobe_base_style", candidate.get("wardrobe_base_style"))

    sig = [str(x).strip() for x in (candidate.get("signature_items") or []) if str(x).strip()]
    if sig and (replace or not (existing.signature_items_json or [])):
        existing.signature_items_json = sig[:8]
    fixed = [str(x).strip() for x in (candidate.get("visual_do_not_change") or []) if str(x).strip()]
    if fixed and (replace or not (existing.visual_do_not_change_json or [])):
        existing.visual_do_not_change_json = fixed[:10]

    evidence = [str(x).strip() for x in (candidate.get("evidence") or []) if str(x).strip()]
    history = existing.evidence_json if isinstance(existing.evidence_json, list) else []
    for item in evidence[:4]:
        history.append({"chapter_num": chapter_num, "evidence": item[:200]})
    existing.evidence_json = history[-20:]
    existing.confidence = max(existing_conf, confidence)
    existing.updated_chapter_num = chapter_num
    existing.metadata_ = {
        **(existing.metadata_ or {}),
        "last_merge_policy": render_prompt("character_profile_merge_policy").strip(),
        "last_update_chapter": chapter_num,
    }


def update_character_profiles_incremental(
    *,
    db: Session,
    novel_id: int,
    novel_version_id: int | None = None,
    chapter_num: int,
    content: str,
    prewrite: dict,
    extracted_facts: dict[str, Any] | None,
    target_language: str,
    strategy: str | None,
) -> None:
    """Update hard-identity character profiles with chapter-local evidence only."""
    _upsert_stub_profiles_from_prewrite(db, novel_id, novel_version_id, prewrite)
    db.flush()
    chars = ((prewrite or {}).get("specification") or {}).get("characters") or []
    if not isinstance(chars, list):
        return

    provider, model = get_model_for_stage(strategy or "web-novel", "reviewer")
    llm = get_llm_with_fallback(provider, model)
    extracted = extracted_facts or {}
    profile_map = {row.character_key: row for row in list_character_profiles(db, novel_id, novel_version_id)}
    processed_keys: set[str] = set()

    for c in chars[:80]:
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        if not _should_process_character(name, content, extracted):
            continue
        key = normalize_character_key(name)
        if not key:
            continue
        if key in processed_keys:
            continue
        row = profile_map.get(key)
        if not row:
            try:
                with db.begin_nested():
                    row = StoryCharacterProfile(
                        novel_id=novel_id,
                        novel_version_id=novel_version_id,
                        character_key=key,
                        display_name=name,
                        evidence_json=[],
                        metadata_={"source": "incremental"},
                    )
                    db.add(row)
                    db.flush()
            except IntegrityError:
                row = db.execute(
                    select(StoryCharacterProfile).where(
                        StoryCharacterProfile.novel_id == novel_id,
                        StoryCharacterProfile.novel_version_id == novel_version_id,
                        StoryCharacterProfile.character_key == key,
                    )
                ).scalar_one_or_none()
                if row is None:
                    raise
            profile_map[key] = row
        processed_keys.add(key)

        prompt = render_prompt(
            "character_profile_increment_extract",
            character_name=name,
            target_language=target_language,
            chapter_num=chapter_num,
            chapter_excerpt=(content or "")[:2800],
            existing_profile_json=json.dumps(
                {
                    "display_name": row.display_name,
                    "gender_presentation": row.gender_presentation,
                    "age_band": row.age_band,
                    "skin_tone": row.skin_tone,
                    "ethnicity": row.ethnicity,
                    "body_type": row.body_type,
                    "face_features": row.face_features,
                    "hair_style": row.hair_style,
                    "hair_color": row.hair_color,
                    "eye_color": row.eye_color,
                    "wardrobe_base_style": row.wardrobe_base_style,
                    "signature_items_json": row.signature_items_json or [],
                    "visual_do_not_change_json": row.visual_do_not_change_json or [],
                },
                ensure_ascii=False,
            ),
            chapter_facts_json=json.dumps(extracted, ensure_ascii=False)[:1200],
        )
        try:
            resp = llm.invoke(prompt)
            payload = _safe_json_loads(str(getattr(resp, "content", "") or ""))
            _merge_profile(row, payload, chapter_num)
        except Exception as exc:
            logger.warning("character profile incremental update failed novel=%s chapter=%s name=%s err=%s", novel_id, chapter_num, name, exc)
