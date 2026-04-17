"""Offline evaluation report for generation behavior metrics.

Usage:
  UV_CACHE_DIR=.uv-cache uv run python scripts/evaluate_generation_metrics.py
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from statistics import median

from sqlalchemy import select, func
from sqlalchemy.exc import OperationalError

from app.core.database import SessionLocal
from app.models.novel import ChapterVersion, GenerationCheckpoint, Novel, NovelVersion
from app.services.generation.evaluation_metrics import (
    compute_abrupt_ending_risk,
    compute_closure_action_metrics,
)


@dataclass
class NovelMetric:
    """单本小说的离线质量评估结果。"""
    novel_id: int
    title: str
    oscillation_rate: float
    abrupt_score: float
    abrupt_risk: bool
    unresolved_mainline: bool
    consistency_conflict_rate: float
    progress_signal_median: float


def _default_version(db, novel: Novel) -> NovelVersion | None:
    """返回小说的默认版本，离线评估只基于默认版本统计。"""
    return db.execute(
        select(NovelVersion).where(
            NovelVersion.novel_id == novel.id,
            NovelVersion.is_default == 1,
        )
    ).scalar_one_or_none()


def _is_evaluable_novel(db, novel: Novel) -> bool:
    """判断一本小说是否具备最小评估条件。"""
    version = _default_version(db, novel)
    if version is None:
        return False

    completed_chapter_count = (
        db.execute(
            select(func.count())
            .select_from(ChapterVersion)
            .where(
                ChapterVersion.novel_version_id == version.id,
                ChapterVersion.status == "completed",
                ChapterVersion.content.is_not(None),
                ChapterVersion.content != "",
            )
        ).scalar_one()
        or 0
    )
    if int(completed_chapter_count) < 3:
        return False

    closure_count = (
        db.execute(
            select(func.count())
            .select_from(GenerationCheckpoint)
            .where(
                GenerationCheckpoint.novel_id == novel.id,
                GenerationCheckpoint.node == "closure_gate",
            )
        ).scalar_one()
        or 0
    )
    if int(closure_count) <= 0:
        return False

    progress_rows = (
        db.execute(
            select(GenerationCheckpoint.state_json)
            .where(
                GenerationCheckpoint.novel_id == novel.id,
                GenerationCheckpoint.node == "chapter_done",
            )
        )
        .all()
    )
    return any(isinstance(row[0], dict) and "progress_signal" in row[0] for row in progress_rows)


def _evaluate_one(db, novel: Novel) -> NovelMetric:
    """评估单本小说的收束、突兀结尾和一致性等核心指标。"""
    version = _default_version(db, novel)
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
    unresolved_mainline = int(latest_state.get("unresolved_count") or 0) > 0
    tails: list[str] = []
    if version is not None:
        tails = (
            db.execute(
                select(ChapterVersion.content)
                .where(ChapterVersion.novel_version_id == version.id)
                .order_by(ChapterVersion.chapter_num.desc())
                .limit(3)
            )
            .scalars()
            .all()
        )
    abrupt = compute_abrupt_ending_risk(latest_state, tails)
    total_consistency = (
        db.execute(
            select(func.count())
            .select_from(GenerationCheckpoint)
            .where(
                GenerationCheckpoint.novel_id == novel.id,
                GenerationCheckpoint.node.in_(("consistency_check", "consistency_blocked")),
            )
        ).scalar_one()
        or 0
    )
    blocked_consistency = (
        db.execute(
            select(func.count())
            .select_from(GenerationCheckpoint)
            .where(
                GenerationCheckpoint.novel_id == novel.id,
                GenerationCheckpoint.node == "consistency_blocked",
            )
        ).scalar_one()
        or 0
    )
    consistency_conflict_rate = float(blocked_consistency) / max(1, int(total_consistency))
    progress_rows = (
        db.execute(
            select(GenerationCheckpoint.state_json)
            .where(
                GenerationCheckpoint.novel_id == novel.id,
                GenerationCheckpoint.node == "chapter_done",
            )
            .order_by(GenerationCheckpoint.id.asc())
        )
        .all()
    )
    signals = [
        float((row[0] or {}).get("progress_signal") or 0.0)
        for row in progress_rows
        if isinstance(row[0], dict)
    ]
    return NovelMetric(
        novel_id=int(novel.id),
        title=str(novel.title or f"novel-{novel.id}"),
        oscillation_rate=float(closure_metrics.get("oscillation_rate") or 0.0),
        abrupt_score=float(abrupt.get("score") or 0.0),
        abrupt_risk=bool(abrupt.get("is_abrupt")),
        unresolved_mainline=unresolved_mainline,
        consistency_conflict_rate=round(consistency_conflict_rate, 4),
        progress_signal_median=round(float(median(signals)), 4) if signals else 0.0,
    )


def _build_report(metrics: list[NovelMetric]) -> dict:
    """把逐本指标汇总成整体报告和高风险小说列表。"""
    oscillation_rates = [m.oscillation_rate for m in metrics]
    abrupt_flags = [1 if m.abrupt_risk else 0 for m in metrics]
    abrupt_scores = [m.abrupt_score for m in metrics]
    unresolved_flags = [1 if m.unresolved_mainline else 0 for m in metrics]
    consistency_rates = [m.consistency_conflict_rate for m in metrics]
    progress_medians = [m.progress_signal_median for m in metrics]
    return {
        "summary": {
            "novels": len(metrics),
            "action_oscillation_rate_median": round(float(median(oscillation_rates)), 4),
            "action_oscillation_rate_mean": round(sum(oscillation_rates) / max(1, len(oscillation_rates)), 4),
            "abrupt_ending_rate": round(sum(abrupt_flags) / max(1, len(abrupt_flags)), 4),
            "abrupt_ending_score_median": round(float(median(abrupt_scores)), 4),
            "unresolved_mainline_rate": round(sum(unresolved_flags) / max(1, len(unresolved_flags)), 4),
            "consistency_conflict_rate_mean": round(sum(consistency_rates) / max(1, len(consistency_rates)), 4),
            "progress_signal_median": round(float(median(progress_medians)), 4),
        },
        "top_risky_novels": [
            {
                "novel_id": m.novel_id,
                "title": m.title,
                "oscillation_rate": m.oscillation_rate,
                "abrupt_score": m.abrupt_score,
                "unresolved_mainline": m.unresolved_mainline,
                "consistency_conflict_rate": m.consistency_conflict_rate,
                "progress_signal_median": m.progress_signal_median,
            }
            for m in sorted(
                metrics,
                key=lambda x: (x.abrupt_score, x.oscillation_rate, x.consistency_conflict_rate),
                reverse=True,
            )[:20]
        ],
    }


def _enforce_thresholds(report: dict) -> list[str]:
    """按预设阈值检查整体验证报告是否需要阻断。"""
    summary = report.get("summary") or {}
    violations: list[str] = []
    if float(summary.get("abrupt_ending_rate") or 0.0) > 0.08:
        violations.append("abrupt_ending_rate > 0.08")
    if float(summary.get("unresolved_mainline_rate") or 0.0) > 0.05:
        violations.append("unresolved_mainline_rate > 0.05")
    if float(summary.get("action_oscillation_rate_mean") or 0.0) > 0.10:
        violations.append("action_oscillation_rate_mean > 0.10")
    if float(summary.get("progress_signal_median") or 0.0) < 0.55:
        violations.append("progress_signal_median < 0.55")
    return violations


def main() -> None:
    """生成离线质量评估报告，并可选按阈值返回非零退出码。"""
    parser = argparse.ArgumentParser(description="Offline generation quality metrics")
    parser.add_argument("--enforce-thresholds", action="store_true", help="Exit non-zero when quality thresholds are violated")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        novels = db.execute(
            select(Novel).where(Novel.status == "completed").order_by(Novel.id.desc()).limit(500)
        ).scalars().all()
        metrics = [_evaluate_one(db, n) for n in novels if _is_evaluable_novel(db, n)]
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

    report = _build_report(metrics)
    violations = _enforce_thresholds(report) if args.enforce_thresholds else []
    if violations:
        report["quality_gate"] = {"passed": False, "violations": violations}
    elif args.enforce_thresholds:
        report["quality_gate"] = {"passed": True, "violations": []}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if violations:
        sys.exit(2)


if __name__ == "__main__":
    main()
