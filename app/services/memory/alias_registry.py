"""Alias registry service (#2).

把角色全名 / 昵称 / 头衔登记到 ``alias_registry`` 表，并提供 trie 风格的
最长匹配 ``resolve_aliases(text)``。Phase 0 不做 LLM 抽取，仅暴露：

- ``register_alias(...)``：单条登记（写入 + flag 检查）。
- ``bulk_register(...)``：批量登记（用于从 outline 同步）。
- ``resolve_aliases(text)``：返回 text 中命中的 ``(start, end, character_key,
  alias)`` 列表，按位置升序、长度降序优先（最长匹配）。

flag ``consistency.alias_registry_v1`` 关闭时：
- ``register_*`` 接口仍可写表（GitOps & 数据准备阶段不阻断）。
- ``resolve_aliases`` 直接返回 ``[]``（不影响现有 NER 路径）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.feature_flags import is_enabled
from app.models.novel import AliasRegistry

logger = logging.getLogger(__name__)


_ALLOWED_TYPES = {"surface", "nickname", "title", "honorific"}


@dataclass
class AliasMatch:
    start: int
    end: int
    character_key: str
    alias: str
    alias_type: str
    priority: int


def register_alias(
    db: Session,
    *,
    novel_version_id: int,
    character_key: str,
    alias: str,
    alias_type: str = "surface",
    priority: int = 0,
) -> AliasRegistry | None:
    """单条登记（idempotent — UNIQUE(novel_version_id, alias) 命中即跳过）。"""
    alias = (alias or "").strip()
    character_key = (character_key or "").strip()
    if not alias or not character_key:
        return None
    if alias_type not in _ALLOWED_TYPES:
        alias_type = "surface"
    row = AliasRegistry(
        novel_version_id=novel_version_id,
        character_key=character_key,
        alias=alias,
        alias_type=alias_type,
        priority=int(priority or 0),
    )
    db.add(row)
    try:
        db.commit()
        db.refresh(row)
        return row
    except IntegrityError:
        db.rollback()
        # 已存在 → 返回已有记录
        return (
            db.execute(
                select(AliasRegistry)
                .where(AliasRegistry.novel_version_id == novel_version_id)
                .where(AliasRegistry.alias == alias)
            )
            .scalar_one_or_none()
        )


def bulk_register(
    db: Session,
    *,
    novel_version_id: int,
    items: list[dict],
) -> int:
    """批量登记。``items`` 每条 ``{character_key, alias, alias_type?, priority?}``。

    与 ``register_alias`` 的区别：
    - **只走一次 commit**：N 条 alias 不再产生 N 次 round-trip / N 次事务。
      旧实现一条调一次 ``register_alias``，每条 commit + IntegrityError 回滚。
      章节 outline 一次推 30+ alias 时这里是个明显热点。
    - **同一批次内自然去重**：先按 ``alias`` 去重，再用 ``existing_aliases``
      过滤掉 DB 中已有的，避免触发 UNIQUE 冲突。
    - **单条 IntegrityError 不阻塞**：极端并发下另一个进程刚插入了同 alias，
      捕获后跳过该条，已经写入的其他行不会丢失。
    """
    if not items:
        return 0

    candidates: dict[str, dict[str, str | int]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        alias = str(item.get("alias") or "").strip()
        character_key = str(item.get("character_key") or "").strip()
        if not alias or not character_key:
            continue
        alias_type = str(item.get("alias_type") or "surface")
        if alias_type not in _ALLOWED_TYPES:
            alias_type = "surface"
        # 同批次同 alias 取第一条（与 DB 上的 UNIQUE 行为一致）
        candidates.setdefault(
            alias,
            {
                "character_key": character_key,
                "alias": alias,
                "alias_type": alias_type,
                "priority": int(item.get("priority") or 0),
            },
        )
    if not candidates:
        return 0

    existing_aliases: set[str] = set(
        db.execute(
            select(AliasRegistry.alias)
            .where(AliasRegistry.novel_version_id == novel_version_id)
            .where(AliasRegistry.alias.in_(list(candidates.keys())))
        )
        .scalars()
        .all()
    )

    written = 0
    new_rows = [
        AliasRegistry(novel_version_id=novel_version_id, **payload)
        for alias_key, payload in candidates.items()
        if alias_key not in existing_aliases
    ]
    if not new_rows:
        return 0
    db.add_all(new_rows)
    try:
        db.flush()
        written = len(new_rows)
    except IntegrityError:
        # 并发冲突：退化到逐条 SAVEPOINT，已落库的不丢
        db.rollback()
        for row in new_rows:
            try:
                with db.begin_nested():
                    db.add(row)
                written += 1
            except IntegrityError:
                continue
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.warning(
            "alias_registry bulk commit conflict, novel_version_id=%s",
            novel_version_id,
        )
        return 0
    return written


def list_aliases(db: Session, *, novel_version_id: int) -> list[AliasRegistry]:
    return list(
        db.execute(
            select(AliasRegistry)
            .where(AliasRegistry.novel_version_id == novel_version_id)
            .order_by(AliasRegistry.priority.desc(), AliasRegistry.id.asc())
        )
        .scalars()
        .all()
    )


def resolve_aliases(
    text: str,
    *,
    novel_version_id: int,
    db: Session,
    novel_id: int | None = None,
) -> list[AliasMatch]:
    """在 ``text`` 中扫描所有 alias 命中。最长匹配优先，position 升序。

    flag ``consistency.alias_registry_v1`` 关闭时直接返回 ``[]``。
    """
    if not is_enabled("consistency.alias_registry_v1", novel_id=novel_id):
        return []
    if not text or not isinstance(text, str):
        return []
    rows = list_aliases(db, novel_version_id=novel_version_id)
    if not rows:
        return []

    # 长度降序、priority 降序
    sorted_rows = sorted(
        rows,
        key=lambda r: (-len(r.alias or ""), -int(r.priority or 0)),
    )
    matches: list[AliasMatch] = []
    used_spans: list[tuple[int, int]] = []  # (start, end) — 已被占用的区间

    def _overlaps(s: int, e: int) -> bool:
        for us, ue in used_spans:
            if s < ue and us < e:
                return True
        return False

    for row in sorted_rows:
        alias = row.alias
        if not alias:
            continue
        start = 0
        while True:
            idx = text.find(alias, start)
            if idx < 0:
                break
            end = idx + len(alias)
            if not _overlaps(idx, end):
                matches.append(
                    AliasMatch(
                        start=idx,
                        end=end,
                        character_key=row.character_key,
                        alias=alias,
                        alias_type=row.alias_type or "surface",
                        priority=int(row.priority or 0),
                    )
                )
                used_spans.append((idx, end))
            start = idx + 1
    matches.sort(key=lambda m: (m.start, -(m.end - m.start)))
    return matches


__all__ = ["AliasMatch", "register_alias", "bulk_register", "list_aliases", "resolve_aliases"]
