"""Storyboard CSV export helpers."""
from __future__ import annotations

import csv
import io

from app.models.storyboard import StoryboardShot


CSV_COLUMNS = [
    "episode_no",
    "scene_no",
    "shot_no",
    "location",
    "time_of_day",
    "shot_size",
    "camera_angle",
    "camera_move",
    "duration_sec",
    "characters",
    "action",
    "dialogue",
    "emotion_beat",
    "transition",
    "sound_hint",
    "production_note",
    "blocking",
    "motivation",
    "performance_note",
    "continuity_anchor",
]


def export_shots_to_csv(shots: list[StoryboardShot]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for shot in shots:
        writer.writerow(
            {
                "episode_no": shot.episode_no,
                "scene_no": shot.scene_no,
                "shot_no": shot.shot_no,
                "location": shot.location or "",
                "time_of_day": shot.time_of_day or "",
                "shot_size": shot.shot_size or "",
                "camera_angle": shot.camera_angle or "",
                "camera_move": shot.camera_move or "",
                "duration_sec": shot.duration_sec,
                "characters": "、".join((shot.characters_json or [])),
                "action": shot.action or "",
                "dialogue": shot.dialogue or "",
                "emotion_beat": shot.emotion_beat or "",
                "transition": shot.transition or "",
                "sound_hint": shot.sound_hint or "",
                "production_note": shot.production_note or "",
                "blocking": shot.blocking or "",
                "motivation": shot.motivation or "",
                "performance_note": shot.performance_note or "",
                "continuity_anchor": shot.continuity_anchor or "",
            }
        )
    return buf.getvalue()
