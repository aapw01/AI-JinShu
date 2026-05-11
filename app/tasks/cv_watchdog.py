"""CV watchdog Celery task (Phase 0 §4.7).

每分钟跑一次：扫 ``presets/flags/registry.yaml`` 列出的全部 flags，
逐个调用 ``evaluate_flag`` 推进 promotion phase。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.services.cv.promotion_engine import evaluate_flag
from app.workers.celery_app import app

logger = logging.getLogger(__name__)


_REGISTRY_PATH = Path(__file__).resolve().parents[2] / "presets" / "flags" / "registry.yaml"


def _load_registry_flags() -> list[str]:
    if not _REGISTRY_PATH.exists():
        return []
    try:
        raw = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.exception("cv_watchdog: failed to read flag registry")
        return []
    flags = raw.get("flags") or {}
    if not isinstance(flags, dict):
        return []
    return sorted(flags.keys())


def run_watchdog_once() -> dict[str, Any]:
    """对全部 registered flags 跑一次评估。返回每个 flag 的决策结果。"""
    out: dict[str, Any] = {"evaluated": [], "skipped": []}
    for flag_name in _load_registry_flags():
        try:
            decision = evaluate_flag(flag_name)
            out["evaluated"].append(
                {
                    "flag": flag_name,
                    "verdict": decision.verdict,
                    "phase": decision.next_phase,
                    "canary_pct": decision.next_canary_pct,
                    "samples": decision.sli.samples,
                    "failure_rate": decision.sli.failure_rate,
                    "reason": decision.reason,
                }
            )
        except Exception as exc:
            logger.exception("cv_watchdog: evaluate_flag(%s) failed", flag_name)
            out["skipped"].append({"flag": flag_name, "error": str(exc)})
    return out


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def cv_watchdog_task(self) -> dict[str, Any]:
    return run_watchdog_once()
