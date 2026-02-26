"""Offline evaluation report for generation behavior metrics.

Usage:
  UV_CACHE_DIR=.uv-cache uv run python scripts/evaluate_generation_metrics.py
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from statistics import median

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.core.database import SessionLocal
from app.models.novel import Chapter, GenerationCheckpoint, Novel
from app.services.generation.evaluation_metrics import (
    compute_abrupt_ending_risk,
    compute_closure_action_metrics,
)


@dataclass
class NovelMetric:
    novel_id: int
    title: str
    oscillation_rate: float
    abrupt_score: float
    abrupt_risk: bool


def _evaluate_one(db, novel: Novel) -> NovelMetric:
    closure_rows = (
        db.execute(
            select(GenerationCheckpoint)
            .where(
                GenerationCheckpoint.novel_id == novel.id,
                GenerationCheckpoint.node == "closure_gate",
            )
            .order_by(GenerationCheckpoint.id.asc())
        )
        .scalars()
        .all()
    )
    actions = [str((r.state_json or {}).get("action") or "") for r in closure_rows]
    closure_metrics = compute_closure_action_metrics(actions)
    latest_state = (closure_rows[-1].state_json or {}) if closure_rows else {}
    tails = (
        db.execute(
            select(Chapter.content)
            .where(Chapter.novel_id == novel.id)
            .order_by(Chapter.chapter_num.desc())
            .limit(3)
        )
        .scalars()
        .all()
    )
    abrupt = compute_abrupt_ending_risk(latest_state, tails)
    return NovelMetric(
        novel_id=int(novel.id),
        title=str(novel.title or f"novel-{novel.id}"),
        oscillation_rate=float(closure_metrics.get("oscillation_rate") or 0.0),
        abrupt_score=float(abrupt.get("score") or 0.0),
        abrupt_risk=bool(abrupt.get("is_abrupt")),
    )


def main() -> None:
    db = SessionLocal()
    try:
        novels = db.execute(
            select(Novel).where(Novel.status.in_(("completed", "generating", "failed"))).order_by(Novel.id.desc()).limit(500)
        ).scalars().all()
        metrics = [_evaluate_one(db, n) for n in novels]
    except OperationalError as e:
        print(
            json.dumps(
                {
                    "error": "database_unreachable",
                    "message": str(e).splitlines()[0],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    finally:
        db.close()

    if not metrics:
        print(json.dumps({"summary": {"novels": 0}}, ensure_ascii=False, indent=2))
        return

    oscillation_rates = [m.oscillation_rate for m in metrics]
    abrupt_flags = [1 if m.abrupt_risk else 0 for m in metrics]
    abrupt_scores = [m.abrupt_score for m in metrics]
    report = {
        "summary": {
            "novels": len(metrics),
            "action_oscillation_rate_median": round(float(median(oscillation_rates)), 4),
            "action_oscillation_rate_mean": round(sum(oscillation_rates) / max(1, len(oscillation_rates)), 4),
            "abrupt_ending_rate": round(sum(abrupt_flags) / max(1, len(abrupt_flags)), 4),
            "abrupt_ending_score_median": round(float(median(abrupt_scores)), 4),
        },
        "top_risky_novels": [
            {
                "novel_id": m.novel_id,
                "title": m.title,
                "oscillation_rate": m.oscillation_rate,
                "abrupt_score": m.abrupt_score,
            }
            for m in sorted(metrics, key=lambda x: (x.abrupt_score, x.oscillation_rate), reverse=True)[:20]
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
