"""Style catalog and recommendation for storyboard creation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.novel import Novel
from app.prompts import render_prompt


@dataclass(slots=True)
class GenreStyle:
    """GenreStyle。"""
    key: str
    label: str
    description: str
    tags: list[str]


@dataclass(slots=True)
class DirectorStyle:
    """DirectorStyle。"""
    key: str
    label: str
    description: str
    camera_notes: list[str]


GENRE_STYLES: list[GenreStyle] = [
    GenreStyle("film_realism", "影视现实", "写实场景与人物动机，强调可拍性", ["现实", "生活", "人物关系"]),
    GenreStyle("anime_stylized", "动漫化", "高能动作与视觉夸张表达", ["夸张", "热血", "高概念"]),
    GenreStyle("horror_thriller", "恐怖惊悚", "压迫感与未知威胁推进", ["惊悚", "悬疑", "压迫"]),
    GenreStyle("web_drama", "网剧都市", "快节奏冲突和强话题性", ["都市", "爽点", "反转"]),
    GenreStyle("mystery_reversal", "悬疑反转", "信息延迟揭示，层层翻面", ["悬疑", "反转", "线索"]),
    GenreStyle("romance_emotional", "情感言情", "关系拉扯与情绪爆点", ["言情", "情绪", "关系"]),
    GenreStyle("sci_fi_highconcept", "科幻高概念", "世界规则驱动冲突", ["科幻", "规则", "设定"]),
]

DIRECTOR_STYLES: list[DirectorStyle] = [
    DirectorStyle("fast_conflict", "快切冲突型", "高密度信息切换，冲突先行", ["快切", "近景", "冲突前置"]),
    DirectorStyle("emotional_longtake", "情绪长镜型", "长镜头承载情绪层次", ["长镜", "停顿", "情绪递进"]),
    DirectorStyle("suspense_reversal", "悬疑反转型", "铺线索、控信息、后反转", ["埋线", "误导", "翻面"]),
    DirectorStyle("commercial_feelgood", "商业爽剧型", "爽点密集，反馈及时", ["爽点", "打脸", "节奏紧凑"]),
    DirectorStyle("anime_action", "动漫动作型", "动作调度和节拍爆发", ["动作", "节拍", "视觉冲击"]),
]


def list_style_presets() -> dict[str, list[dict[str, Any]]]:
    """列出stylepresets。"""
    return {
        "genre_styles": [
            {"key": g.key, "label": g.label, "description": g.description, "tags": g.tags} for g in GENRE_STYLES
        ],
        "director_styles": [
            {
                "key": d.key,
                "label": d.label,
                "description": d.description,
                "camera_notes": d.camera_notes,
            }
            for d in DIRECTOR_STYLES
        ],
    }


def _match_score(text: str, tags: list[str]) -> float:
    """执行 match score 相关辅助逻辑。"""
    if not text:
        return 0.0
    score = 0.0
    for tag in tags:
        if tag and tag in text:
            score += 1.0
    return score


def recommend_styles(novel: Novel, chapter_text: str = "") -> list[dict[str, Any]]:
    """执行 recommend styles 相关辅助逻辑。"""
    corpus = " ".join(
        [
            str(novel.title or ""),
            str(novel.genre or ""),
            str(novel.style or ""),
            chapter_text[:3000],
        ]
    )

    ranked: list[dict[str, Any]] = []
    for g in GENRE_STYLES:
        g_score = _match_score(corpus, g.tags)
        for d in DIRECTOR_STYLES:
            d_score = _match_score(corpus, d.camera_notes + [d.label])
            bonus = 0.0
            if g.key == "horror_thriller" and d.key == "suspense_reversal":
                bonus += 0.8
            if g.key == "anime_stylized" and d.key == "anime_action":
                bonus += 0.8
            if g.key in {"web_drama", "mystery_reversal"} and d.key == "fast_conflict":
                bonus += 0.6
            if g.key == "romance_emotional" and d.key == "emotional_longtake":
                bonus += 0.6
            raw = g_score * 0.58 + d_score * 0.32 + bonus + 0.2
            ranked.append(
                {
                    "genre_style_key": g.key,
                    "genre_style_label": g.label,
                    "director_style_key": d.key,
                    "director_style_label": d.label,
                    "confidence": round(min(0.99, 0.45 + raw / 8.0), 4),
                    "reason": render_prompt(
                        "storyboard_style_recommend_reason",
                        genre_label=g.label,
                        director_label=d.label,
                    ).strip(),
                }
            )

    ranked.sort(key=lambda x: x["confidence"], reverse=True)
    top = ranked[:3]
    if not top:
        top = [
            {
                "genre_style_key": "web_drama",
                "genre_style_label": "网剧都市",
                "director_style_key": "fast_conflict",
                "director_style_label": "快切冲突型",
                "confidence": 0.6,
                "reason": "默认推荐：适配多数短剧场景",
            }
        ]
    return top


def find_genre_style(key: str | None) -> GenreStyle | None:
    """执行 find genre style 相关辅助逻辑。"""
    if not key:
        return None
    return next((g for g in GENRE_STYLES if g.key == key), None)


def find_director_style(key: str | None) -> DirectorStyle | None:
    """执行 find director style 相关辅助逻辑。"""
    if not key:
        return None
    return next((d for d in DIRECTOR_STYLES if d.key == key), None)
