"""Shot-level expansion with director-oriented fields."""
from __future__ import annotations

from dataclasses import dataclass

from app.prompts import render_prompt
from app.services.storyboard.adapter import DirectorIntent, PlatformIntent
from app.services.storyboard.scene_planner import EpisodePlan, ScenePlan


@dataclass(slots=True)
class ShotDraft:
    episode_no: int
    scene_no: int
    shot_no: int
    location: str
    time_of_day: str
    shot_size: str
    camera_angle: str
    camera_move: str
    duration_sec: int
    characters_json: list[str]
    action: str
    dialogue: str
    emotion_beat: str
    transition: str
    sound_hint: str
    production_note: str
    blocking: str
    motivation: str
    performance_note: str
    continuity_anchor: str


VERTICAL_SHOT_PATTERN = [
    ("特写", "平", "静", "冲突引爆"),
    ("近景", "侧", "跟", "情绪反应"),
    ("中景", "俯", "推", "信息揭示"),
]

HORIZONTAL_SHOT_PATTERN = [
    ("全景", "平", "静", "空间交代"),
    ("中景", "侧", "移", "关系推进"),
    ("近景", "平", "推", "情绪聚焦"),
    ("特写", "仰", "静", "反转强调"),
]


def expand_shots(
    *,
    episode: EpisodePlan,
    scene: ScenePlan,
    lane: str,
    platform: PlatformIntent,
    director: DirectorIntent,
) -> list[ShotDraft]:
    pattern = VERTICAL_SHOT_PATTERN if lane == "vertical_feed" else HORIZONTAL_SHOT_PATTERN
    drafts: list[ShotDraft] = []
    for idx, (shot_size, angle, move, motivation) in enumerate(pattern, start=1):
        action = render_prompt(
            "storyboard_shot_action",
            scene_purpose=scene.purpose,
            location=scene.location,
            episode_pivot=episode.pivot[:20],
        ).strip()
        dialogue = _dialogue_line(scene, lane, idx)
        continuity_anchor = render_prompt(
            "storyboard_shot_continuity_anchor",
            shot_idx=idx,
            episode_hook=episode.hook[:16],
        ).strip()
        drafts.append(
            ShotDraft(
                episode_no=episode.episode_no,
                scene_no=scene.scene_no,
                shot_no=idx,
                location=scene.location,
                time_of_day=scene.time_of_day,
                shot_size=shot_size,
                camera_angle=angle,
                camera_move=move,
                duration_sec=platform.avg_shot_seconds,
                characters_json=["主角", "对手"],
                action=action,
                dialogue=dialogue,
                emotion_beat=scene.emotion_beat,
                transition="切",
                sound_hint="鼓点渐强" if lane == "vertical_feed" else "氛围垫乐",
                production_note=(
                    "优先手机友好构图，保证主体占画面中心" if lane == "vertical_feed"
                    else "注意前后景层次，保证调度连贯"
                ),
                blocking=render_prompt(
                    "storyboard_shot_blocking",
                    emotion_beat=scene.emotion_beat,
                ).strip(),
                motivation=motivation,
                performance_note=render_prompt(
                    "storyboard_shot_performance_note",
                    emotion_beat=scene.emotion_beat,
                ).strip(),
                continuity_anchor=continuity_anchor,
            )
        )
    return drafts


def _dialogue_line(scene: ScenePlan, lane: str, shot_idx: int) -> str:
    if lane == "vertical_feed":
        variants = [
            "你现在退一步，我就当没发生。",
            "想赢？先回答你到底怕什么。",
            "今天不说清楚，谁也别走。",
        ]
    else:
        variants = [
            "你以为你在救人，其实你在重复同一个错误。",
            "我们之间的问题，从来不是输赢，而是你不敢面对真相。",
            "如果今天还回避，明天就没有回头路。",
            "把话说完，然后我们各自承担后果。",
        ]
    return variants[(shot_idx - 1) % len(variants)]
