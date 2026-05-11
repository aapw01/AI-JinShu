"""Post-chapter-commit hooks (§A 业务主路径接线).

在 ``node_finalize`` 完成 chapter 写库后调用，串接所有"被动审计"agent：

- #4 ``spacetime_extractor.extract_and_persist``
- #5 ``voice_drift.audit_chapter_voice`` （flag 控）
- #6 ``foreshadow_lifecycle.advance_foreshadows``
- #7 ``outline_auditor.audit_chapter_outline`` → must_fix → #8 ``patch_writer``

设计原则：

- 每个 hook 独立 try/except，**绝不阻塞** chapter commit。
- 每个 hook 自己读 flag。这里只负责调度顺序和参数收集。
- 所有 LLM agent 已经走 ``run_llm_agent`` → ``emit_agent_event`` 自动写
  cost / tokens 列；这里不做二次记账。
- ``patch_writer`` 接到 ``must_fix`` 时只**生成**补丁文本，**不**直接覆写章节。
  覆写策略要走 reviewer-must-pass 通道，会在另一个 PR 里做。
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.database import SessionLocal

logger = logging.getLogger(__name__)


def run_post_chapter_hooks(
    *,
    novel_id: int,
    novel_version_id: int | None,
    chapter_num: int,
    chapter_text: str,
    chapter_summary: str | None = None,
    outline: dict[str, Any] | None = None,
    sample_size: int = 1,
) -> dict[str, Any]:
    """主入口：按顺序调用各审计 hook，返回每个 hook 的状态摘要。

    返回值仅供日志/调试，**绝不**让上游基于此做控制流决定（防止藕合）。
    """
    summary: dict[str, Any] = {
        "spacetime": "skip",
        "voice_drift": "skip",
        "foreshadow_lifecycle": "skip",
        "outline_audit": "skip",
        "patch_writer": "skip",
        "reader_lens": "skip",
    }

    # #2 alias registry：把 outline.character_aliases 自动入库（无需 LLM）
    try:
        from app.core.feature_flags import is_enabled

        if is_enabled("consistency.alias_registry_v1", novel_id=novel_id):
            aliases_payload = (
                outline.get("character_aliases") if isinstance(outline, dict) else None
            )
            if aliases_payload:
                _bulk_register_aliases(
                    novel_id=novel_id,
                    novel_version_id=novel_version_id,
                    aliases_payload=aliases_payload,
                )
    except Exception:
        logger.debug("alias hook init failed", exc_info=True)

    # #4 spacetime extract
    try:
        from app.core.feature_flags import is_enabled
        from app.services.agents.spacetime_extractor import extract_and_persist

        if is_enabled("memory.spacetime_anchor_v1", novel_id=novel_id):
            prev_anchor = None
            if isinstance(outline, dict):
                hint = outline.get("spacetime_hint")
                if isinstance(hint, dict):
                    prev_anchor = (hint.get("prev_anchor") or "").strip() or None
            db = SessionLocal()
            try:
                extract_and_persist(
                    db=db,
                    novel_id=novel_id,
                    novel_version_id=novel_version_id,
                    chapter_num=chapter_num,
                    chapter_text=chapter_text,
                    prev_anchor=prev_anchor,
                )
                db.commit()
                summary["spacetime"] = "ok"
            except Exception:
                db.rollback()
                logger.exception("post_chapter_hook spacetime failed")
                summary["spacetime"] = "error"
            finally:
                db.close()
    except Exception:
        logger.debug("spacetime hook init failed", exc_info=True)

    # #6 foreshadow lifecycle（只跑规则，不做 LLM 匹配）
    try:
        from app.core.feature_flags import is_enabled
        from app.services.memory.foreshadow_lifecycle import advance_foreshadows

        if is_enabled("consistency.foreshadow_lifecycle_v1", novel_id=novel_id):
            db = SessionLocal()
            try:
                advance_foreshadows(
                    db,
                    novel_id=novel_id,
                    novel_version_id=novel_version_id,
                    current_chapter=chapter_num,
                )
                db.commit()
                summary["foreshadow_lifecycle"] = "ok"
            except Exception:
                db.rollback()
                logger.exception("post_chapter_hook foreshadow_lifecycle failed")
                summary["foreshadow_lifecycle"] = "error"
            finally:
                db.close()
    except Exception:
        logger.debug("foreshadow hook init failed", exc_info=True)

    # #5 voice drift —— 仅在 outline 提供 main_character 时跑
    try:
        from app.core.feature_flags import is_enabled
        from app.services.agents.voice_drift import audit_chapter_voice

        character_key = _resolve_main_character(outline)
        if (
            character_key
            and is_enabled("style.voice_drift_audit", novel_id=novel_id)
        ):
            db = SessionLocal()
            try:
                audit_chapter_voice(
                    db,
                    novel_id=novel_id,
                    novel_version_id=novel_version_id,
                    chapter_num=chapter_num,
                    character_key=character_key,
                    chapter_text=chapter_text,
                )
                db.commit()
                summary["voice_drift"] = "ok"
            except Exception:
                db.rollback()
                logger.exception("post_chapter_hook voice_drift failed")
                summary["voice_drift"] = "error"
            finally:
                db.close()
    except Exception:
        logger.debug("voice_drift hook init failed", exc_info=True)

    # #7 outline auditor → #8 patch_writer 串联
    try:
        from app.core.feature_flags import is_enabled
        from app.services.agents.outline_auditor import audit_chapter_outline

        contract = _outline_to_contract(outline, chapter_num) if outline else None
        if (
            contract is not None
            and is_enabled("quality.outline_promise_audit", novel_id=novel_id)
        ):
            db = SessionLocal()
            try:
                report = audit_chapter_outline(
                    db,
                    novel_id=novel_id,
                    novel_version_id=novel_version_id,
                    chapter_num=chapter_num,
                    contract=contract,
                    chapter_text=chapter_text,
                )
                if report is not None:
                    summary["outline_audit"] = "ok"
                    failing = [
                        p for p in report.promises if p.fulfilled in {"no", "partial"}
                    ]
                    if failing and is_enabled(
                        "repair.precision_rewrite", novel_id=novel_id
                    ):
                        summary["patch_writer"] = _try_patch(
                            novel_id=novel_id,
                            novel_version_id=novel_version_id,
                            chapter_num=chapter_num,
                            chapter_text=chapter_text,
                            must_fix=failing,
                        )
            except Exception:
                db.rollback()
                logger.exception("post_chapter_hook outline_audit failed")
                summary["outline_audit"] = "error"
            finally:
                db.close()
    except Exception:
        logger.debug("outline_audit hook init failed", exc_info=True)

    # #12 reader lens
    try:
        from app.core.feature_flags import is_enabled
        from app.services.agents.reader_lens import evaluate_chapter

        if is_enabled("quality.reader_lens_v1", novel_id=novel_id):
            db = SessionLocal()
            try:
                evaluate_chapter(
                    db,
                    novel_id=novel_id,
                    novel_version_id=novel_version_id,
                    chapter_num=chapter_num,
                    chapter_text=chapter_text,
                    prior_summary=chapter_summary,
                )
                db.commit()
                summary["reader_lens"] = "ok"
            except Exception:
                db.rollback()
                logger.exception("post_chapter_hook reader_lens failed")
                summary["reader_lens"] = "error"
            finally:
                db.close()
    except Exception:
        logger.debug("reader_lens hook init failed", exc_info=True)

    return summary


def _resolve_main_character(outline: dict[str, Any] | None) -> str | None:
    """从 outline 提取主角 key。

    只信**显式字段**：``main_character`` / ``protagonist`` / ``pov_character``。

    旧实现允许 ``characters[0]`` 兜底，但 ``characters`` 通常是"本章登场人物
    列表"，按写作顺序排第一的极少是 POV 主角，往往是配角或反派。这种兜底
    会让 #5 voice drift 拿错画像对比，制造一堆假告警。**不要再用**——
    宁愿放弃这一章的 voice audit，也不输出错误信号。
    """
    if not isinstance(outline, dict):
        return None
    for key in ("main_character", "protagonist", "pov_character"):
        v = outline.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        # 允许 {"name": "..."} / {"key": "..."} 形式
        if isinstance(v, dict):
            for k in ("name", "key", "id"):
                inner = v.get(k)
                if isinstance(inner, str) and inner.strip():
                    return inner.strip()
    return None


def _outline_to_contract(
    outline: dict[str, Any] | None, chapter_num: int
) -> Any | None:
    """从 outline dict 推断 ``OutlineContract``。

    必须有 ``chapter_objective`` 或 ``objective`` —— 否则 outline 审计没有审计
    目标，结果会变成"满章节都是 unfulfilled promise"的伪报。**不允许**用
    ``title`` 兜底：标题通常是"第N章 风波再起"这种文学性短语，不能作为审计
    对象。缺关键字段直接返回 None，让 outline_auditor 跳过当章。
    """
    if not isinstance(outline, dict):
        return None
    try:
        from app.services.agents.contracts.outline import OutlineContract
    except Exception:
        return None
    objective = outline.get("chapter_objective") or outline.get("objective") or ""
    if not isinstance(objective, str) or not objective.strip():
        return None
    rni = outline.get("required_new_information") or outline.get(
        "key_information"
    ) or []
    if not isinstance(rni, list):
        rni = []
    payoff = outline.get("payoff") or outline.get("emotional_payoff")
    opening = outline.get("opening_scene") or outline.get("opening")
    forbid = outline.get("forbidden_repeats") or []
    if not isinstance(forbid, list):
        forbid = []
    try:
        return OutlineContract(
            chapter_num=int(chapter_num),
            chapter_objective=str(objective).strip(),
            required_new_information=[str(x) for x in rni if isinstance(x, str)],
            payoff=str(payoff) if isinstance(payoff, str) else None,
            opening_scene=str(opening) if isinstance(opening, str) else None,
            forbidden_repeats=[str(x) for x in forbid if isinstance(x, str)],
        )
    except Exception:
        return None


def _bulk_register_aliases(
    *,
    novel_id: int,
    novel_version_id: int | None,
    aliases_payload: Any,
) -> None:
    """把 outline.character_aliases 解开成 ``items``，调 alias_registry 批量入库。"""
    if novel_version_id is None or not aliases_payload:
        return
    items: list[dict[str, Any]] = []
    if isinstance(aliases_payload, list):
        for entry in aliases_payload:
            if not isinstance(entry, dict):
                continue
            canonical = (entry.get("canonical") or "").strip()
            aliases = entry.get("aliases") or []
            if not canonical or not isinstance(aliases, list):
                continue
            # canonical 自身也作为 alias 存
            items.append(
                {"character_key": canonical, "alias": canonical, "alias_type": "canonical"}
            )
            for alias in aliases:
                if not isinstance(alias, str) or not alias.strip():
                    continue
                items.append(
                    {"character_key": canonical, "alias": alias.strip(), "alias_type": "surface"}
                )
    if not items:
        return
    db = SessionLocal()
    try:
        from app.services.memory.alias_registry import bulk_register

        bulk_register(db, novel_version_id=int(novel_version_id), items=items)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("alias bulk register failed novel=%s", novel_id)
    finally:
        db.close()


def _try_patch(
    *,
    novel_id: int,
    novel_version_id: int | None,
    chapter_num: int,
    chapter_text: str,
    must_fix: list[Any],
) -> str:
    """对每条 must_fix 尝试生成精确补丁。**不**直接覆写章节，仅留事件 + 候选 patch。

    返回 ``ok`` / ``noop`` / ``error``：

    - ``ok``    至少一条 must_fix 得到候选补丁；事件已 emit。
    - ``noop``  所有 must_fix 都没匹配到 anchor 或 LLM 输出被规则拦截。
    - ``error`` 不可恢复异常（已记日志）。
    """
    try:
        from app.services.agents.contracts.patch import EditSpan, PatchInstruction
        from app.services.agents.events import emit_agent_event
        from app.services.agents.patch_writer import apply_patch

        ok_count = 0
        for fix in must_fix or []:
            # OutlinePromiseVerdict.evidence_span 是 (start, end) 索引；优先用它
            issue = (
                getattr(fix, "note", None)
                or getattr(fix, "key", None)
                or "outline promise unmet"
            )
            anchor_text: str | None = None
            anchor_idx = -1
            span = getattr(fix, "evidence_span", None)
            if isinstance(span, (list, tuple)) and len(span) == 2:
                try:
                    s = int(span[0])
                    e = int(span[1])
                    if 0 <= s < e <= len(chapter_text):
                        anchor_text = chapter_text[s:e]
                        anchor_idx = s
                except Exception:
                    anchor_text = None
            if anchor_text is None or anchor_idx < 0:
                continue
            anchor_end = anchor_idx + len(anchor_text)
            # 取 anchor 前后各 32 字符做"上下文锚"，patch_writer 需要 anchor_before /
            # anchor_after 来稳定 LLM 输出位置。
            anchor_before = chapter_text[max(0, anchor_idx - 32) : anchor_idx]
            anchor_after = chapter_text[anchor_end : anchor_end + 32]
            if not anchor_before or not anchor_after:
                continue
            instruction = PatchInstruction(
                span=EditSpan(
                    span_start=anchor_idx,
                    span_end=anchor_end,
                    anchor_before=anchor_before,
                    anchor_after=anchor_after,
                    original_text=anchor_text,
                ),
                instruction=str(issue),
                must_keep_characters=[],
                forbid_new_characters=True,
            )
            patch = apply_patch(
                novel_id=novel_id,
                novel_version_id=novel_version_id,
                chapter_num=chapter_num,
                instruction=instruction,
                chapter_text=chapter_text,
            )
            if patch is None:
                continue
            ok_count += 1
            emit_agent_event(
                agent_name="patch_writer",
                event_type="candidate",
                novel_id=novel_id,
                novel_version_id=novel_version_id,
                chapter_num=chapter_num,
                verdict="pass",
                payload={
                    "issue": instruction.instruction[:200],
                    "anchor_start": anchor_idx,
                    "anchor_len": len(anchor_text),
                    "patched_len": len(patch.patched_text),
                    "length_delta": patch.length_delta,
                },
            )
        return "ok" if ok_count > 0 else "noop"
    except Exception:
        logger.exception("patch_writer chain failed")
        return "error"
