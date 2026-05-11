"""Gate config loader (Phase 0 §4.4).

每个 gate 文件 ``presets/gates/<category>.yaml`` 定义一组 ``mode/threshold/
max_outline_revise/downgrade_to`` 类配置；运行时按类目读，5s TTL 缓存，
per-novel override 走文件内 ``overrides.per_novel.<id>``。
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


_CACHE_TTL_SECONDS = 5.0
_PRESETS_ROOT = Path(__file__).resolve().parents[2] / "presets" / "gates"


class GateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["strict", "warn", "off"] = "warn"
    threshold: float | None = None
    max_outline_revise: int = 0
    downgrade_to: str | None = None
    metric_label: str = ""


class GateFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    gates: dict[str, GateConfig] = Field(default_factory=dict)
    overrides: dict[str, dict[str, dict[str, GateConfig]]] = Field(default_factory=dict)


_lock = threading.Lock()
_cache: dict[str, tuple[float, GateFile]] = {}


def _load_file(category_file: Path) -> GateFile:
    with category_file.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return GateFile.model_validate(raw)


def invalidate_gates_cache(category: str | None = None) -> None:
    with _lock:
        if category is None:
            _cache.clear()
        else:
            _cache.pop(category, None)


def _read_category(category: str) -> GateFile | None:
    """读取一个 category 的全部 gate 配置；未注册或解析失败返回 None。"""
    now = time.monotonic()
    with _lock:
        cached = _cache.get(category)
        if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]

    path = _PRESETS_ROOT / f"{category}.yaml"
    if not path.exists():
        return None
    try:
        gf = _load_file(path)
    except Exception:
        logger.exception("gates: failed to parse %s", path)
        return None
    with _lock:
        _cache[category] = (now, gf)
    return gf


_DEFAULT_GATE = GateConfig()


def get_gate(
    category: str, gate_name: str, *, novel_id: int | None = None
) -> GateConfig:
    """读取 ``presets/gates/<category>.yaml`` 的 ``gates.<gate_name>`` 配置。

    优先级：``overrides.per_novel.<novel_id>.<gate_name>`` > ``gates.<gate_name>``
    > 默认（``mode=warn``）。任何 IO / parse 失败一律返回默认（fail-close 友好）。
    """
    gf = _read_category(category)
    if gf is None:
        return _DEFAULT_GATE.model_copy()

    if novel_id is not None:
        per_novel = gf.overrides.get("per_novel", {}).get(str(novel_id), {})
        if gate_name in per_novel:
            return per_novel[gate_name].model_copy()

    return gf.gates.get(gate_name, _DEFAULT_GATE).model_copy()


def list_gates(category: str) -> dict[str, GateConfig]:
    gf = _read_category(category)
    if gf is None:
        return {}
    return {name: gate.model_copy() for name, gate in gf.gates.items()}
