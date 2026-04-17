"""Episode and scene planning for storyboard generation."""
from __future__ import annotations

from dataclasses import dataclass

from app.services.storyboard.adapter import AdaptedChapter


@dataclass(slots=True)
class EpisodePlan:
    """Episode计划。"""
    episode_no: int
    title: str
    hook: str
    pivot: str
    cliffhanger: str
    chapter_refs: list[int]


@dataclass(slots=True)
class ScenePlan:
    """Scene计划。"""
    episode_no: int
    scene_no: int
    location: str
    time_of_day: str
    purpose: str
    emotion_beat: str


def partition_episodes(chapters: list[AdaptedChapter], target_episodes: int) -> list[EpisodePlan]:
    """执行 partition episodes 相关辅助逻辑。"""
    if not chapters:
        return []
    total = len(chapters)
    buckets = max(1, min(target_episodes, total))
    plans: list[EpisodePlan] = []
    for idx in range(buckets):
        start = int(idx * total / buckets)
        end = int((idx + 1) * total / buckets)
        chunk = chapters[start:max(start + 1, end)]
        ref_nums = [c.chapter_num for c in chunk]
        first = chunk[0]
        last = chunk[-1]
        plans.append(
            EpisodePlan(
                episode_no=idx + 1,
                title=f"第{idx + 1}集 {first.title[:24]}",
                hook=(first.summary or first.content[:80] or first.title)[:60],
                pivot=(chunk[len(chunk) // 2].summary or chunk[len(chunk) // 2].title)[:60],
                cliffhanger=(last.summary or last.content[-120:] or last.title)[:60],
                chapter_refs=ref_nums,
            )
        )
    return plans


def decompose_scenes(episode: EpisodePlan, lane: str) -> list[ScenePlan]:
    """执行 decompose scenes 相关辅助逻辑。"""
    scene_count = 3 if lane == "vertical_feed" else 4
    purposes = ["建立冲突", "升级对抗", "反转揭示", "悬念收束"]
    emotions = ["紧张", "压迫", "爆发", "悬念"]
    time_cycle = ["日", "夜", "傍晚", "室内夜"]
    location_base = ["主场景", "过渡场景", "对手场景", "收束场景"]

    scenes: list[ScenePlan] = []
    for i in range(scene_count):
        scenes.append(
            ScenePlan(
                episode_no=episode.episode_no,
                scene_no=i + 1,
                location=location_base[i % len(location_base)],
                time_of_day=time_cycle[i % len(time_cycle)],
                purpose=purposes[i % len(purposes)],
                emotion_beat=emotions[i % len(emotions)],
            )
        )
    return scenes
