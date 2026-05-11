"""Feature flag yaml→DB sync (Phase 0 §4.2 GitOps + DB 副本).

把 ``presets/flags/registry.yaml``（以及未来可能拆出的单文件 ``presets/flags/<name>.yaml``）
里的状态周期推到 DB 副本。同步任务只允许 yaml→DB 单向覆盖；反向（DB→yaml）
由 cv_watchdog 紧急回滚后开 PR 完成（不在本任务中处理）。

使用方式：

- 周期：每 1 分钟跑一次（Celery beat 配置在 ``app/workers/celery_app.py`` 里挂）。
- 测试：直接调用纯函数 ``sync_flags_from_yaml`` 跑断言；Celery 装饰函数不参与单测。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.core.feature_flags import apply_flag_state_from_yaml
from app.workers.celery_app import app

logger = logging.getLogger(__name__)


_DEFAULT_PRESETS_DIR = Path(__file__).resolve().parents[2] / "presets" / "flags"


def _load_yaml_file(path: Path) -> dict[str, Any]:
    """读 yaml；失败抛给调用方 swallow，避免影响下次周期。"""
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"yaml at {path} is not a mapping")
    return data


def _collect_yaml_states(presets_dir: Path) -> dict[str, dict[str, Any]]:
    """合并 ``registry.yaml`` 与每 flag 单文件，单文件优先。

    - ``registry.yaml`` 提供基线（按文档为 14 条改造默认全 disable）。
    - ``<flag_name>.yaml`` 单文件 OVERRIDE 同名条目（拆分文件路径，方便细粒度 CODEOWNERS）。
    """
    if not presets_dir.exists():
        return {}

    out: dict[str, dict[str, Any]] = {}

    registry_path = presets_dir / "registry.yaml"
    if registry_path.exists():
        try:
            data = _load_yaml_file(registry_path)
        except Exception:
            logger.exception("flag_yaml_sync: failed to load %s", registry_path)
            data = {}
        flags_section = data.get("flags") or {}
        if isinstance(flags_section, dict):
            for name, state in flags_section.items():
                if isinstance(state, dict):
                    out[str(name)] = dict(state)

    for path in sorted(presets_dir.glob("*.yaml")):
        if path.name in {"registry.yaml"} or path.name.startswith("_"):
            continue
        try:
            data = _load_yaml_file(path)
        except Exception:
            logger.exception("flag_yaml_sync: failed to load %s", path)
            continue
        flag_name = path.stem
        out[flag_name] = data

    return out


def sync_flags_from_yaml(presets_dir: Path | None = None) -> dict[str, str]:
    """把 yaml 状态推到 DB；返回每个 flag 的 ``synced`` / ``skipped`` / ``failed`` 摘要。

    纯函数，便于单测。Celery 任务包装在 :func:`flag_yaml_sync_task`。
    """
    target_dir = presets_dir or _DEFAULT_PRESETS_DIR
    states = _collect_yaml_states(target_dir)
    summary: dict[str, str] = {}
    for flag_name, state in states.items():
        try:
            applied = apply_flag_state_from_yaml(flag_name, state)
            summary[flag_name] = "synced" if applied is not None else "skipped"
        except Exception:
            logger.exception("flag_yaml_sync: failed for %s", flag_name)
            summary[flag_name] = "failed"
    return summary


@app.task(bind=True, acks_late=True, reject_on_worker_lost=True)
def flag_yaml_sync_task(self) -> dict[str, str]:
    """Celery 周期任务入口（默认每 60s 一次，beat 配置见 celery_app）。"""
    return sync_flags_from_yaml()
