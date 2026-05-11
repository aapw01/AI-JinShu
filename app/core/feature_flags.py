"""Feature flag service (Phase 0 §4.2 / §4.2.1).

设计要点：

- **GitOps + DB 副本**：``presets/flags/<name>.yaml`` 是 source-of-truth，
  ``system_settings`` 表 ``flag.<name>`` key 是运行时副本。本模块只读 DB
  副本（5s TTL 缓存），同步任务在 ``app/tasks/feature_flags.py`` 里。
- **fail-close**：DB / 缓存均不可达时返回 ``False``，绝不 fail-open 给未灰度
  流量（与文档 §4.2 / 附录 B.5 fixture 03 保持一致）。
- **灰度逻辑**：``allowlist 命中`` → ``hash(novel_id) % 100 < rollout_pct`` →
  ``全局 enabled``，依次短路。
- **写入只能由 admin / cv_watchdog / 紧急回滚 CLI 调用**，主路径只能
  ``is_enabled``，靠模块文档 + CI lint 守底（lint 规则后续 PR 加）。

注意：本模块不依赖 ``app/services/agents/events.py`` 的 ``emit_agent_event``，
避免反向依赖；audit 走专表 ``flag_audit_log``，CV / 监控自己消费。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from copy import deepcopy
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.novel import FlagAuditLog, SystemRuntimeSetting

logger = logging.getLogger(__name__)


_FLAG_KEY_PREFIX = "flag."
_CACHE_TTL_SECONDS = 5.0


# 默认状态：当 yaml/db 都没值时返回这个（fail-closed 友好）。
_DEFAULT_FLAG_STATE: dict[str, Any] = {
    "enabled": False,
    "rollout_pct": 0,
    "novel_allowlist": [],
    "owner": "team:unassigned",
    "purpose": "",
    "created_at": None,
    "target_full_rollout_at": None,
    "expected_removal_at": None,
    "depends_on": [],
}


_cache_lock = threading.Lock()
_cache_ts: float = 0.0
_cache_value: dict[str, dict[str, Any]] = {}


# ---------------- cache ----------------


def _is_cache_fresh(cache_ts: float) -> bool:
    """判断缓存窗口是否未过期。"""
    return (time.monotonic() - float(cache_ts or 0.0)) < _CACHE_TTL_SECONDS


def invalidate_flags_cache(flag_name: str | None = None) -> None:
    """清空 flag 缓存。``flag_name=None`` 时清全部。

    ``set_flag`` 必须在 toggle 后调用本函数；分布式部署中需要由 Redis pub/sub
    或 DB notify 通知其它 worker 同步清空（接入路径在 §4.2 已写明，本 PR 仅
    保证进程内一致性）。
    """
    global _cache_ts
    with _cache_lock:
        if flag_name is None:
            _cache_value.clear()
            _cache_ts = 0.0
        else:
            _cache_value.pop(flag_name, None)
            # 局部 invalidate 不重置整体 ts，否则会触发整张表 reload；
            # 设为 0 强制下次 _load_all 一次性补齐。
            _cache_ts = 0.0


def _load_all_from_db(db: Session) -> dict[str, dict[str, Any]]:
    """读全部 ``flag.*`` 记录。任意异常上抛由调用方决定 fail-close。"""
    rows = (
        db.execute(
            select(SystemRuntimeSetting).where(
                SystemRuntimeSetting.setting_key.like(f"{_FLAG_KEY_PREFIX}%")
            )
        )
        .scalars()
        .all()
    )
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        name = row.setting_key[len(_FLAG_KEY_PREFIX) :]
        if not name:
            continue
        merged = dict(_DEFAULT_FLAG_STATE)
        if isinstance(row.setting_value_json, dict):
            merged.update(row.setting_value_json)
        out[name] = merged
    return out


def _read_all() -> dict[str, dict[str, Any]] | None:
    """带缓存读全部 flag。失败返回 ``None`` —— 调用方据此 fail-close。"""
    global _cache_ts, _cache_value
    with _cache_lock:
        if _is_cache_fresh(_cache_ts) and _cache_value:
            return deepcopy(_cache_value)
    try:
        db = SessionLocal()
    except Exception:
        logger.exception("feature_flags: SessionLocal failed; fail-close")
        return None
    try:
        data = _load_all_from_db(db)
    except Exception:
        logger.exception("feature_flags: load from db failed; fail-close")
        return None
    finally:
        try:
            db.close()
        except Exception:
            logger.debug("feature_flags: session close failed", exc_info=True)
    with _cache_lock:
        _cache_value = data
        _cache_ts = time.monotonic()
        return deepcopy(_cache_value)


def get_flag_state(flag_name: str) -> dict[str, Any] | None:
    """读取单个 flag 完整状态（含默认值合并）。失败 / 未注册时返回 ``None``。"""
    if not flag_name:
        return None
    data = _read_all()
    if data is None:
        return None
    return data.get(flag_name)


# ---------------- evaluation ----------------


def _hash_bucket(novel_id: int) -> int:
    """把 ``novel_id`` 映射到 0–99 桶，灰度按 ``< rollout_pct`` 命中。

    用 SHA-256 做哈希避免 Python ``hash()`` 进程间不一致 + 加盐避免和其它
    桶共享分布。盐固定，确保同一 novel 在不同进程算出同一桶。
    """
    h = hashlib.sha256(f"feature_flags:{int(novel_id)}".encode("utf-8")).hexdigest()
    return int(h[:8], 16) % 100


def is_enabled(flag_name: str, *, novel_id: int | None = None) -> bool:
    """判定某个 flag 是否对当前调用启用（fail-close 默认 False）。

    短路顺序：

    1. flag 服务自身故障（DB / 缓存均不可达）→ ``False``。
    2. 命中 ``novel_allowlist`` → ``True``。
    3. ``hash(novel_id) % 100 < rollout_pct`` → ``True``。
    4. 全局 ``enabled`` 字段。
    """
    if not flag_name:
        return False
    data = _read_all()
    if data is None:
        return False
    state = data.get(flag_name)
    if state is None:
        return False

    if novel_id is not None:
        try:
            allowlist = state.get("novel_allowlist") or []
            if int(novel_id) in {int(x) for x in allowlist}:
                return True
        except (TypeError, ValueError):
            logger.debug("novel_allowlist contains non-int values; ignoring", exc_info=True)
        try:
            pct = int(state.get("rollout_pct") or 0)
        except (TypeError, ValueError):
            pct = 0
        if pct > 0 and _hash_bucket(novel_id) < pct:
            return True

    return bool(state.get("enabled"))


# ---------------- write side ----------------


_KNOWN_CHANGED_BY_PREFIXES = ("user:", "ci:", "incident:", "cv_watchdog", "yaml_sync", "test")


def _normalize_changed_by(changed_by: str) -> str:
    """``changed_by`` 必须是结构化标识；不允许空白或纯随机字串混入审计表。"""
    cleaned = (changed_by or "").strip()
    if not cleaned:
        raise ValueError("changed_by is required for set_flag")
    if not any(cleaned.startswith(p) for p in _KNOWN_CHANGED_BY_PREFIXES):
        # 不阻塞写入但记录 warning，方便事后回查。
        logger.warning(
            "set_flag changed_by uses non-standard prefix; allowed=%s",
            _KNOWN_CHANGED_BY_PREFIXES,
        )
    return cleaned


def _validate_set_flag_kwargs(
    enabled: bool | None,
    rollout_pct: int | None,
    novel_allowlist: list[int] | None,
) -> None:
    """阻挡明显错误的写入；其它字段后续 PR 再补 Pydantic 校验。"""
    if rollout_pct is not None and not 0 <= int(rollout_pct) <= 100:
        raise ValueError("rollout_pct must be in [0, 100]")
    if novel_allowlist is not None:
        for value in novel_allowlist:
            try:
                int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"novel_allowlist must contain integers; got {value!r}") from exc
    if enabled is not None and not isinstance(enabled, (bool, int)):
        raise ValueError("enabled must be a boolean")


def set_flag(
    flag_name: str,
    *,
    enabled: bool | None = None,
    rollout_pct: int | None = None,
    novel_allowlist: list[int] | None = None,
    extra: Mapping[str, Any] | None = None,
    changed_by: str,
    reason: str,
) -> dict[str, Any]:
    """改 DB 副本 + 写 ``flag_audit_log`` + invalidate cache。

    ``extra`` 用于设置 owner / purpose / created_at / target_full_rollout_at /
    expected_removal_at / depends_on 等元字段。``changed_by`` 必填，``reason``
    必填（合规审计要求）。

    返回写入后的完整 flag 状态（合并默认值）。失败抛 ValueError。
    """
    if not flag_name:
        raise ValueError("flag_name is required")
    if not reason or not reason.strip():
        raise ValueError("reason is required for set_flag (audit)")
    actor = _normalize_changed_by(changed_by)
    _validate_set_flag_kwargs(enabled, rollout_pct, novel_allowlist)

    key = f"{_FLAG_KEY_PREFIX}{flag_name}"
    db = SessionLocal()
    try:
        row = (
            db.execute(
                select(SystemRuntimeSetting).where(SystemRuntimeSetting.setting_key == key)
            )
            .scalars()
            .one_or_none()
        )
        before = (
            dict(row.setting_value_json)
            if row is not None and isinstance(row.setting_value_json, dict)
            else {}
        )

        merged = dict(_DEFAULT_FLAG_STATE)
        merged.update(before)
        if enabled is not None:
            merged["enabled"] = bool(enabled)
        if rollout_pct is not None:
            merged["rollout_pct"] = int(rollout_pct)
        if novel_allowlist is not None:
            merged["novel_allowlist"] = [int(x) for x in novel_allowlist]
        if extra:
            for k, v in dict(extra).items():
                if k in {"enabled", "rollout_pct", "novel_allowlist"}:
                    raise ValueError(
                        f"set_flag.extra cannot override controlled field {k!r}; "
                        "use the dedicated keyword instead"
                    )
                merged[k] = v

        if row is None:
            row = SystemRuntimeSetting(setting_key=key, setting_value_json=merged)
            db.add(row)
        else:
            row.setting_value_json = merged

        audit = FlagAuditLog(
            flag_name=flag_name,
            changed_by=actor,
            before_state=before,
            after_state=merged,
            reason=reason.strip(),
        )
        db.add(audit)
        db.commit()
        invalidate_flags_cache(flag_name)

        # Prometheus：记录 toggle 方向
        try:
            before_enabled = bool(before.get("enabled"))
            after_enabled = bool(merged.get("enabled"))
            if before_enabled != after_enabled:
                from app.core.metrics import flag_toggle_total

                flag_toggle_total.inc(
                    flag=flag_name,
                    direction="on" if after_enabled else "off",
                )
        except Exception:
            logger.debug("flag_toggle metric failed", exc_info=True)
        return deepcopy(merged)
    except Exception:
        try:
            db.rollback()
        except Exception:
            logger.debug("set_flag rollback failed", exc_info=True)
        raise
    finally:
        try:
            db.close()
        except Exception:
            logger.debug("set_flag session close failed", exc_info=True)


# ---------------- yaml sync (GitOps) ----------------


def apply_flag_state_from_yaml(
    flag_name: str,
    state: Mapping[str, Any],
    *,
    changed_by: str = "yaml_sync",
    reason: str = "yaml→db sync (presets/flags/)",
) -> dict[str, Any] | None:
    """yaml → DB 同步入口（被 ``app/tasks/feature_flags`` 周期调用）。

    若 DB 与 yaml 已一致则跳过（不写 audit）；否则按 ``set_flag`` 写并落 audit。
    返回合并后的状态；跳过返回 ``None``。
    """
    if not isinstance(state, Mapping):
        raise ValueError("yaml flag state must be a mapping")

    db = SessionLocal()
    try:
        row = (
            db.execute(
                select(SystemRuntimeSetting).where(
                    SystemRuntimeSetting.setting_key == f"{_FLAG_KEY_PREFIX}{flag_name}"
                )
            )
            .scalars()
            .one_or_none()
        )
        before = (
            dict(row.setting_value_json)
            if row is not None and isinstance(row.setting_value_json, dict)
            else {}
        )
    finally:
        try:
            db.close()
        except Exception:
            logger.debug("apply_flag_state_from_yaml session close failed", exc_info=True)

    target = dict(_DEFAULT_FLAG_STATE)
    target.update(state)

    if before == target:
        return None

    return set_flag(
        flag_name,
        enabled=bool(target.get("enabled")),
        rollout_pct=int(target.get("rollout_pct") or 0),
        novel_allowlist=[int(x) for x in (target.get("novel_allowlist") or [])],
        extra={
            k: v
            for k, v in target.items()
            if k not in {"enabled", "rollout_pct", "novel_allowlist"}
        },
        changed_by=changed_by,
        reason=reason,
    )
