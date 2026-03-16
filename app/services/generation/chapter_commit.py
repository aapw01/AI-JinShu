"""Chapter commit service — all post-finalize memory write-back in one place.

Consolidates summary, character state, character profile, bible store,
quality report, and checkpoint writes that were previously scattered
inside _node_finalize / _write_longform_artifacts.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

from app.services.generation.common import REVIEW_SCORE_THRESHOLD
from app.services.generation.heuristics import extract_item_mentions, extract_timeline_markers
from app.services.generation.progress import volume_no_for_chapter
from app.services.generation.state import GenerationState


def write_longform_artifacts(
    state: GenerationState,
    chapter_num: int,
    summary_text: str,
    final_content: str,
    language_score: float,
    aesthetic_score_val: float,
    revision_count: int,
    extracted_facts: dict[str, Any] | None = None,
    db: Session | None = None,
) -> None:
    """Persist all post-chapter artifacts to bible/quality/checkpoint stores."""
    bible = state["bible_store"]
    quality = state["quality_store"]
    checkpoint = state["checkpoint_store"]

    outline = state.get("outline") or {}
    volume_no = int(state.get("volume_no") or volume_no_for_chapter(state, chapter_num))

    if chapter_num == state.get("start_chapter", 1):
        chars = ((state.get("prewrite") or {}).get("specification") or {}).get("characters") or []
        if isinstance(chars, list):
            for c in chars[:80]:
                if isinstance(c, dict) and c.get("name"):
                    bible.upsert_entity(
                        novel_id=state["novel_id"],
                        novel_version_id=state.get("novel_version_id"),
                        entity_type="character",
                        name=str(c["name"]),
                        status="alive",
                        summary=str(c.get("description") or c.get("role") or "")[:300],
                        metadata={"source": "specification"},
                        db=db,
                    )

    event_id = f"EV-{chapter_num:04d}"
    bible.add_event(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        event_id=event_id,
        chapter_num=chapter_num,
        title=outline.get("title") or f"第{chapter_num}章",
        event_type="chapter",
        payload={
            "summary": summary_text[:600],
            "review_score": state.get("score"),
            "language_score": language_score,
        },
        db=db,
    )

    foreshadowing = outline.get("foreshadowing")
    if foreshadowing:
        items = foreshadowing if isinstance(foreshadowing, list) else [foreshadowing]
        for idx, item in enumerate(items[:8], start=1):
            text = str(item)
            matched = re.findall(r"[A-Z]-\d+", text)
            fs_id = matched[0] if matched else f"FS-{chapter_num:04d}-{idx}"
            bible.upsert_foreshadow(
                novel_id=state["novel_id"],
                novel_version_id=state.get("novel_version_id"),
                foreshadow_id=fs_id,
                title=text[:200],
                planted_chapter=chapter_num,
                state="planted",
                payload={"outline": outline.get("title")},
                db=db,
            )

    payoff = str(outline.get("payoff") or "").strip()
    if payoff:
        matched = re.findall(r"[A-Z]-\d+", payoff)
        for fs_id in matched[:5]:
            bible.upsert_foreshadow(
                novel_id=state["novel_id"],
                novel_version_id=state.get("novel_version_id"),
                foreshadow_id=fs_id,
                title=payoff[:200],
                planted_chapter=max(1, chapter_num - 1),
                state="resolved",
                resolved_chapter=chapter_num,
                payload={"payoff": payoff[:500]},
                db=db,
            )

    extracted = extracted_facts or {}
    for ev in (extracted.get("events") or [])[:20]:
        if not isinstance(ev, dict):
            continue
        eid = str(ev.get("id") or f"EVX-{chapter_num:04d}-{abs(hash(str(ev))) % 100000:05d}")[:64]
        bible.add_event(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            event_id=eid,
            chapter_num=chapter_num,
            title=str(ev.get("title") or "")[:255] or f"第{chapter_num}章事件",
            event_type=str(ev.get("type") or "extracted")[:100],
            actors=[str(x) for x in (ev.get("actors") or [])][:10],
            payload={
                "summary": str(ev.get("summary") or "")[:500],
                "time_marker": str(ev.get("time_marker") or "")[:80],
            },
            db=db,
        )

    extracted_entities: dict[str, Any] = {}
    for ent in (extracted.get("entities") or [])[:30]:
        if not isinstance(ent, dict) or not ent.get("name"):
            continue
        entity = bible.upsert_entity(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            entity_type=str(ent.get("entity_type") or "unknown")[:50],
            name=str(ent.get("name"))[:255],
            status=str(ent.get("status") or "active")[:50],
            summary=str(ent.get("summary") or "")[:300],
            metadata={"source": "fact_extractor"},
            db=db,
        )
        extracted_entities[entity.name] = entity

    for fact in (extracted.get("facts") or [])[:40]:
        if not isinstance(fact, dict):
            continue
        entity_name = str(fact.get("entity_name") or "").strip()
        if not entity_name:
            continue
        entity = extracted_entities.get(entity_name)
        if entity is None:
            entity = bible.upsert_entity(
                novel_id=state["novel_id"],
                novel_version_id=state.get("novel_version_id"),
                entity_type="unknown",
                name=entity_name[:255],
                status="active",
                summary="",
                metadata={"source": "fact_extractor_fact_only"},
                db=db,
            )
        bible.add_fact(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            entity_id=entity.id,
            fact_type=str(fact.get("fact_type") or "attribute")[:100],
            value_json={"value": fact.get("value"), "chapter_num": chapter_num},
            chapter_from=chapter_num,
            db=db,
        )

    for marker in extract_timeline_markers(final_content):
        bible.add_event(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            event_id=f"TL-{chapter_num:04d}-{abs(hash(marker)) % 10000:04d}",
            chapter_num=chapter_num,
            title=f"时间标记:{marker}",
            event_type="timeline_marker",
            payload={"marker": marker},
            db=db,
        )

    for item_name in extract_item_mentions(final_content):
        item_entity = bible.upsert_entity(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            entity_type="item",
            name=item_name,
            status="active",
            summary=f"在第{chapter_num}章出现",
            metadata={"source": "chapter_content"},
            db=db,
        )
        bible.add_fact(
            novel_id=state["novel_id"],
            novel_version_id=state.get("novel_version_id"),
            entity_id=item_entity.id,
            fact_type="mentioned_in_chapter",
            value_json={"chapter_num": chapter_num, "context": final_content[:300]},
            chapter_from=chapter_num,
            db=db,
        )

    chars = bible.list_entities(
        state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        entity_type="character",
        db=db,
    )
    appeared = [c for c in chars if c.name and c.name in final_content]
    for i in range(len(appeared)):
        for j in range(i + 1, len(appeared)):
            a = appeared[i]
            b = appeared[j]
            bible.add_event(
                novel_id=state["novel_id"],
                novel_version_id=state.get("novel_version_id"),
                event_id=f"REL-{chapter_num:04d}-{a.id}-{b.id}",
                chapter_num=chapter_num,
                title=f"{a.name}/{b.name} 关系信号",
                event_type="relationship_signal",
                actors=[a.name, b.name],
                payload={"signal": "co_appearance"},
                db=db,
            )

    factual_score = float(state.get("factual_score", 0.0) or 0.0)
    reviewer_aesthetic = float(state.get("aesthetic_review_score", 0.0) or 0.0)
    verdict = "pass" if (
        state.get("score", 0.0) >= REVIEW_SCORE_THRESHOLD
        and factual_score >= 0.65
        and language_score >= 0.6
        and reviewer_aesthetic >= 0.6
    ) else "warning"
    quality.add_report(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        scope="chapter",
        scope_id=str(chapter_num),
        metrics_json={
            "review_score": state.get("score", 0.0),
            "factual_score": factual_score,
            "language_score": language_score,
            "aesthetic_review_score": reviewer_aesthetic,
            "aesthetic_score": aesthetic_score_val,
            "revision_count": revision_count,
            "volume_no": volume_no,
            "consistency_scorecard": state.get("consistency_scorecard") or {},
            "review_gate": state.get("review_gate") or {},
        },
        verdict=verdict,
        db=db,
    )

    if state.get("task_id"):
        checkpoint.save_checkpoint(
            task_id=state["task_id"],
            novel_id=state["novel_id"],
            volume_no=volume_no,
            chapter_num=chapter_num,
            node="chapter_done",
            state_json={
                "chapter_num": chapter_num,
                "volume_no": volume_no,
                "review_score": state.get("score", 0.0),
                "language_score": language_score,
                "consistency_scorecard": state.get("consistency_scorecard") or {},
                "review_gate": state.get("review_gate") or {},
                "summary": summary_text[:400],
                "content_preview": final_content[:400],
            },
            db=db,
        )

    is_volume_end = int(chapter_num) == int(state.get("segment_end_chapter") or state["end_chapter"])
    if is_volume_end:
        _write_volume_report(state, chapter_num, volume_no, quality, bible, checkpoint, db=db)


def _write_volume_report(
    state: GenerationState,
    chapter_num: int,
    volume_no: int,
    quality: Any,
    bible: Any,
    checkpoint: Any,
    db: Session | None = None,
) -> None:
    """Write end-of-volume quality report and snapshot."""
    chapter_reports = quality.list_reports(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        scope="chapter",
        db=db,
    )
    current_volume_reports = [r for r in chapter_reports if int((r.metrics_json or {}).get("volume_no") or 0) == volume_no]
    review_scores = [
        float((r.metrics_json or {}).get("review_score") or 0.0)
        for r in current_volume_reports
        if (r.metrics_json or {}).get("review_score") is not None
    ]
    language_scores = [
        float((r.metrics_json or {}).get("language_score") or 0.0)
        for r in current_volume_reports
        if (r.metrics_json or {}).get("language_score") is not None
    ]
    aesthetic_scores = [
        float((r.metrics_json or {}).get("aesthetic_score") or 0.0)
        for r in current_volume_reports
        if (r.metrics_json or {}).get("aesthetic_score") is not None
    ]
    blocked_count = sum(1 for r in current_volume_reports if bool((r.metrics_json or {}).get("blocked")))
    avg_review = round(sum(review_scores) / max(len(review_scores), 1), 4)
    avg_language = round(sum(language_scores) / max(len(language_scores), 1), 4)
    avg_aesthetic = round(sum(aesthetic_scores) / max(len(aesthetic_scores), 1), 4)
    evidence_chain: list[dict[str, Any]] = []
    if blocked_count > 0:
        evidence_chain.append({"metric": "blocked_chapters", "value": blocked_count, "threshold": 0, "status": "fail"})
    if avg_review < REVIEW_SCORE_THRESHOLD:
        evidence_chain.append(
            {
                "metric": "avg_review_score",
                "value": avg_review,
                "threshold": REVIEW_SCORE_THRESHOLD,
                "status": "warning" if avg_review >= 0.6 else "fail",
            }
        )
    if avg_language < 0.65:
        evidence_chain.append(
            {
                "metric": "avg_language_score",
                "value": avg_language,
                "threshold": 0.65,
                "status": "warning" if avg_language >= 0.58 else "fail",
            }
        )
    if avg_aesthetic < 0.62:
        evidence_chain.append(
            {
                "metric": "avg_aesthetic_score",
                "value": avg_aesthetic,
                "threshold": 0.62,
                "status": "warning",
            }
        )
    if blocked_count > 0 or avg_review < 0.6 or avg_language < 0.58:
        volume_verdict = "fail"
    elif avg_review < REVIEW_SCORE_THRESHOLD or avg_language < 0.65 or avg_aesthetic < 0.62:
        volume_verdict = "warning"
    else:
        volume_verdict = "pass"

    bible.save_snapshot(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        volume_no=volume_no,
        chapter_end=chapter_num,
        snapshot_json={
            "chapter_end": chapter_num,
            "token_usage_input": state.get("total_input_tokens", 0),
            "token_usage_output": state.get("total_output_tokens", 0),
            "estimated_cost": state.get("estimated_cost", 0.0),
            "avg_review_score": avg_review,
            "avg_language_score": avg_language,
            "avg_aesthetic_score": avg_aesthetic,
            "blocked_chapters": blocked_count,
            "evidence_chain": evidence_chain,
        },
        db=db,
    )
    quality.add_report(
        novel_id=state["novel_id"],
        novel_version_id=state.get("novel_version_id"),
        scope="volume",
        scope_id=str(volume_no),
        metrics_json={
            "chapter_end": chapter_num,
            "volume_no": volume_no,
            "estimated_cost": state.get("estimated_cost", 0.0),
            "avg_review_score": avg_review,
            "avg_language_score": avg_language,
            "avg_aesthetic_score": avg_aesthetic,
            "blocked_chapters": blocked_count,
            "chapter_count": len(current_volume_reports),
            "gate_triggered": volume_verdict != "pass",
            "evidence_chain": evidence_chain,
        },
        verdict=volume_verdict,
        db=db,
    )
    if state.get("task_id"):
        checkpoint.save_checkpoint(
            task_id=state["task_id"],
            novel_id=state["novel_id"],
            volume_no=volume_no,
            chapter_num=chapter_num,
            node="volume_gate",
            state_json={
                "volume_no": volume_no,
                "chapter_end": chapter_num,
                "verdict": volume_verdict,
                "evidence_chain": evidence_chain,
                "next_replan_mode": "aggressive" if volume_verdict == "fail" else ("focus" if volume_verdict == "warning" else "baseline"),
            },
            db=db,
        )
