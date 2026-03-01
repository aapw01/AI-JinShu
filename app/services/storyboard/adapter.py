"""Novel to storyboard adaptation helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AdaptedChapter:
    chapter_num: int
    title: str
    summary: str
    content: str


@dataclass(slots=True)
class StyleIntent:
    genre: str
    style: str
    tone: str
    conflict_density: str
    style_tags: list[str]


@dataclass(slots=True)
class DirectorIntent:
    camera_language: str
    pacing_goal: str
    performance_focus: str
    emotion_curve: str


@dataclass(slots=True)
class PlatformIntent:
    lane: str
    target_seconds: int
    avg_shot_seconds: int
    closeup_ratio: float
    reversal_density: str


@dataclass(slots=True)
class HardConstraints:
    must_keep_characters: list[str]
    must_keep_events: list[str]
    forbidden_new_mainline: bool


def extract_style_intent(genre: str | None, style: str | None, novel_title: str, chapters: list[AdaptedChapter]) -> StyleIntent:
    genre_val = (genre or "通用").strip() or "通用"
    style_val = (style or "网文通用").strip() or "网文通用"
    sample = "\n".join([c.title + " " + c.summary for c in chapters[:5]])

    tone = "高压紧凑"
    if any(k in sample for k in ("温情", "治愈", "日常")):
        tone = "温和细腻"
    elif any(k in sample for k in ("悬疑", "危机", "反转", "阴谋")):
        tone = "悬念压迫"
    elif any(k in sample for k in ("热血", "战争", "复仇")):
        tone = "激烈热血"

    conflict_density = "high" if any(k in sample for k in ("冲突", "危机", "追杀", "对抗", "反转")) else "medium"

    style_tags = [genre_val, style_val, tone, "章节驱动", "人物关系递进"]
    if "都市" in genre_val or "言情" in genre_val:
        style_tags.append("情绪对手戏")
    if "悬疑" in genre_val or "科幻" in genre_val:
        style_tags.append("信息揭示")

    return StyleIntent(
        genre=genre_val,
        style=style_val,
        tone=tone,
        conflict_density=conflict_density,
        style_tags=style_tags,
    )


def build_director_intent(style_intent: StyleIntent, lane: str) -> DirectorIntent:
    if lane == "vertical_feed":
        return DirectorIntent(
            camera_language="高近景占比，快速切换，冲突先行",
            pacing_goal="3-8秒出现信息增量或冲突推进",
            performance_focus="表情变化和短促动作反应",
            emotion_curve="每场至少一次情绪峰值",
        )
    return DirectorIntent(
        camera_language="建立镜头+动作镜头+反应镜头组接",
        pacing_goal="稳定递进，关键节点做情绪停顿",
        performance_focus="人物关系与心理层次表演",
        emotion_curve="铺垫-爆发-余韵的三段情绪线",
    )


def build_platform_intent(lane: str, target_episode_seconds: int) -> PlatformIntent:
    if lane == "vertical_feed":
        return PlatformIntent(
            lane=lane,
            target_seconds=target_episode_seconds,
            avg_shot_seconds=3,
            closeup_ratio=0.62,
            reversal_density="high",
        )
    return PlatformIntent(
        lane=lane,
        target_seconds=target_episode_seconds,
        avg_shot_seconds=5,
        closeup_ratio=0.35,
        reversal_density="medium",
    )


def build_hard_constraints(chapters: list[AdaptedChapter]) -> HardConstraints:
    core_events: list[str] = []
    core_characters: list[str] = []
    for chapter in chapters[:8]:
        title = chapter.title.strip()
        if title:
            core_events.append(title)
        for token in (chapter.summary or "").replace("，", " ").replace("。", " ").split():
            if len(token) >= 2 and token[0].isupper() is False:
                continue
        # Keep extraction lightweight and deterministic; character list can be empty.
    return HardConstraints(
        must_keep_characters=core_characters[:8],
        must_keep_events=core_events[:10],
        forbidden_new_mainline=True,
    )


def prompt_contract(
    *,
    style_intent: StyleIntent,
    director_intent: DirectorIntent,
    platform_intent: PlatformIntent,
    hard_constraints: HardConstraints,
) -> dict[str, Any]:
    return {
        "StyleIntent": {
            "genre": style_intent.genre,
            "style": style_intent.style,
            "tone": style_intent.tone,
            "conflict_density": style_intent.conflict_density,
            "style_tags": style_intent.style_tags,
        },
        "DirectorIntent": {
            "camera_language": director_intent.camera_language,
            "pacing_goal": director_intent.pacing_goal,
            "performance_focus": director_intent.performance_focus,
            "emotion_curve": director_intent.emotion_curve,
        },
        "PlatformIntent": {
            "lane": platform_intent.lane,
            "target_seconds": platform_intent.target_seconds,
            "avg_shot_seconds": platform_intent.avg_shot_seconds,
            "closeup_ratio": platform_intent.closeup_ratio,
            "reversal_density": platform_intent.reversal_density,
        },
        "HardConstraints": {
            "must_keep_characters": hard_constraints.must_keep_characters,
            "must_keep_events": hard_constraints.must_keep_events,
            "forbidden_new_mainline": hard_constraints.forbidden_new_mainline,
        },
    }
