"""Heuristic scoring, reviewer payload normalisation, and review gate logic."""
from __future__ import annotations

import re
from typing import Any


def aesthetic_score(text: str) -> float:
    """Heuristic readability/aesthetic score in 0-1."""
    if not text:
        return 0.0
    paragraphs = [p for p in text.splitlines() if p.strip()]
    sentence_count = max(1, len(re.findall(r"[。！？!?\.]", text)))
    avg_sentence_len = len(text) / sentence_count
    paragraph_bonus = min(len(paragraphs) / 12.0, 1.0) * 0.2
    rhythm = 1.0 - min(abs(avg_sentence_len - 28) / 60.0, 1.0)
    return max(0.0, min(1.0, 0.62 + paragraph_bonus + rhythm * 0.25))


def extract_timeline_markers(text: str) -> list[str]:
    """提取timelinemarkers。"""
    patterns = [
        r"第[一二三四五六七八九十百\d]+天",
        r"次日",
        r"翌日",
        r"当晚",
        r"[一二三四五六七八九十\d]+日后",
        r"[一二三四五六七八九十\d]+小时后",
    ]
    results: list[str] = []
    for p in patterns:
        for m in re.findall(p, text):
            if m not in results:
                results.append(m)
    return results[:10]


def extract_item_mentions(text: str) -> list[str]:
    """提取itemmentions。"""
    quoted = re.findall(r'["""]([^"""]{2,12})["""]', text)
    item_like = [x.strip() for x in quoted if any(k in x for k in ["剑", "刀", "符", "印", "戒", "卷", "令", "丹", "石"])]
    dedup: list[str] = []
    for x in item_like:
        if x and x not in dedup:
            dedup.append(x)
    return dedup[:10]


def chapter_progress_signal(
    outline: dict[str, Any],
    summary_text: str,
    final_content: str,
    extracted_facts: dict[str, Any] | None,
    review_score: float,
    factual_score: float,
) -> float:
    """Heuristic chapter progression signal in 0-1."""
    events = extracted_facts.get("events") if isinstance(extracted_facts, dict) else []
    events_count = len(events or [])
    summary_len = len((summary_text or "").strip())
    payoff = str(outline.get("payoff") or "").strip()
    purpose = str(outline.get("purpose") or "").strip()
    mini_climax = str(outline.get("mini_climax") or "").strip().lower()
    suspense = str(outline.get("suspense_level") or "").strip()
    has_conflict_word = any(k in final_content for k in ["冲突", "对峙", "反转", "危机", "爆发", "背叛", "抉择"])

    signal = 0.0
    signal += min(events_count / 6.0, 1.0) * 0.30
    signal += min(summary_len / 260.0, 1.0) * 0.15
    signal += (0.15 if payoff else 0.0)
    signal += (0.10 if purpose else 0.0)
    signal += (0.10 if mini_climax not in {"", "none", "无"} else 0.0)
    signal += (0.08 if suspense in {"中", "高", "高强"} else 0.03 if suspense else 0.0)
    signal += (0.07 if has_conflict_word else 0.0)
    signal += max(0.0, min(1.0, review_score)) * 0.03
    signal += max(0.0, min(1.0, factual_score)) * 0.02
    return max(0.0, min(1.0, signal))


def safe_issue(item: Any, severity: str = "should_fix") -> dict[str, Any]:
    """执行 safe issue 相关辅助逻辑。"""
    if not isinstance(item, dict):
        return {"category": "general", "severity": severity, "claim": str(item or ""), "evidence": "", "confidence": 0.55}
    return {
        "category": str(item.get("category") or "general")[:40],
        "severity": str(item.get("severity") or severity)[:20],
        "claim": str(item.get("claim") or "")[:220],
        "evidence": str(item.get("evidence") or "")[:120],
        "confidence": max(0.0, min(1.0, float(item.get("confidence", 0.55) or 0.55))),
    }


def evidence_valid(issue: dict[str, Any], draft: str) -> bool:
    """执行 evidence valid 相关辅助逻辑。"""
    evidence_text = str(issue.get("evidence") or "").strip()
    if not evidence_text:
        return False
    if len(evidence_text) < 2:
        return False
    return evidence_text in draft


def build_consistency_scorecard(report: Any) -> dict[str, Any]:
    """构建consistencyscorecard。"""
    issues = list(getattr(report, "issues", []) or [])
    blockers = list(getattr(report, "blockers", []) or [])
    warnings = list(getattr(report, "warnings", []) or [])
    category_counts: dict[str, int] = {}
    for i in issues:
        key = str(getattr(i, "category", "unknown") or "unknown")
        category_counts[key] = category_counts.get(key, 0) + 1
    score = max(0.0, min(1.0, 1.0 - (len(blockers) * 0.32) - (len(warnings) * 0.08)))
    reason_codes: list[str] = []
    for cat, n in sorted(category_counts.items()):
        if n > 0:
            reason_codes.append(f"{cat}:{n}")
    return {
        "score": round(score, 4),
        "passed": bool(getattr(report, "passed", False)),
        "blockers": len(blockers),
        "warnings": len(warnings),
        "categories": category_counts,
        "reason_codes": reason_codes[:8],
        "issues": [
            {
                "level": str(getattr(i, "level", "")),
                "category": str(getattr(i, "category", "")),
                "message": str(getattr(i, "message", ""))[:220],
            }
            for i in issues[:12]
        ],
    }


def normalize_reviewer_payload(result: Any, default_feedback: str = "") -> dict[str, Any]:
    """把 reviewer payload 规范化为统一格式。"""
    if isinstance(result, dict):
        score = float(result.get("score", 0.75) or 0.75)
        return {
            "score": max(0.0, min(1.0, score)),
            "confidence": max(0.0, min(1.0, float(result.get("confidence", 0.6) or 0.6))),
            "feedback": str(result.get("feedback", default_feedback or "")),
            "must_fix": [safe_issue(x, "must_fix") for x in (result.get("must_fix") or [])][:4],
            "should_fix": [safe_issue(x, "should_fix") for x in (result.get("should_fix") or [])][:4],
            "positives": [str(x)[:120] for x in (result.get("positives") or result.get("highlights") or []) if str(x).strip()][:6],
            "risks": [str(x)[:120] for x in (result.get("risks") or []) if str(x).strip()][:6],
            "contradictions": [str(x)[:180] for x in (result.get("contradictions") or []) if str(x).strip()][:10],
            "raw": result,
        }
    if isinstance(result, tuple):
        if len(result) >= 3:
            score, feedback, third = result[0], result[1], result[2]
            extra = [str(x) for x in (third or [])][:6] if isinstance(third, list) else []
            return {
                "score": max(0.0, min(1.0, float(score or 0.75))),
                "confidence": 0.55,
                "feedback": str(feedback or default_feedback),
                "must_fix": [],
                "should_fix": [],
                "positives": extra,
                "risks": [],
                "contradictions": extra,
                "raw": {},
            }
        if len(result) >= 2:
            score, feedback = result[0], result[1]
            return {
                "score": max(0.0, min(1.0, float(score or 0.75))),
                "confidence": 0.55,
                "feedback": str(feedback or default_feedback),
                "must_fix": [],
                "should_fix": [],
                "positives": [],
                "risks": [],
                "contradictions": [],
                "raw": {},
            }
    return {
        "score": 0.75,
        "confidence": 0.4,
        "feedback": default_feedback,
        "must_fix": [],
        "should_fix": [],
        "positives": [],
        "risks": ["invalid_reviewer_payload"],
        "contradictions": [],
        "raw": {},
    }


def normalize_progression_payload(result: Any, default_feedback: str = "") -> dict[str, Any]:
    """把 progression payload 规范化为统一格式。"""
    payload = normalize_reviewer_payload(result, default_feedback)
    raw = payload.get("raw")
    if not isinstance(raw, dict):
        raw = result if isinstance(result, dict) else {}
    for field in [
        "duplicate_beats",
        "no_new_delta",
        "repeated_reveal",
        "repeated_relationship_turn",
        "transition_conflict",
        "foreshadow_check",
    ]:
        payload[field] = [str(x)[:180] for x in (raw.get(field) or []) if str(x).strip()][:8]
    return payload


def extract_ai_flavor(reviewer_output: dict[str, Any]) -> dict[str, Any]:
    """提取aiflavor。"""
    raw = reviewer_output.get("ai_flavor") or {}
    if not isinstance(raw, dict):
        raw = {}
    score = raw.get("score", 5)
    try:
        score = max(0, min(10, int(float(score))))
    except (TypeError, ValueError):
        score = 5
    issues = [str(x)[:200] for x in (raw.get("issues") or []) if str(x).strip()][:8]
    return {"score": score, "issues": issues}


def extract_webnovel_principles(reviewer_output: dict[str, Any]) -> dict[str, Any]:
    """提取webnovelprinciples。"""
    raw = reviewer_output.get("webnovel_principles") or {}
    if not isinstance(raw, dict):
        raw = {}
    score = raw.get("score", 5)
    try:
        score = max(0, min(10, int(float(score))))
    except (TypeError, ValueError):
        score = 5
    violations = [str(x)[:200] for x in (raw.get("violations") or []) if str(x).strip()][:6]
    return {"score": score, "violations": violations}


def build_review_gate(draft: str, *payloads: dict[str, Any]) -> dict[str, Any]:
    """构建审校gate。"""
    payload_list = [p for p in payloads if isinstance(p, dict)]
    all_must_fix: list[dict[str, Any]] = []
    confidences: list[float] = []
    scores: list[float] = []
    for payload in payload_list:
        all_must_fix.extend(list(payload.get("must_fix") or []))
        confidences.append(float(payload.get("confidence", 0.0) or 0.0))
        scores.append(float(payload.get("score", 0.0) or 0.0))
    validated: list[dict[str, Any]] = []
    weak: list[dict[str, Any]] = []
    for issue in all_must_fix:
        conf = float(issue.get("confidence", 0.0) or 0.0)
        if evidence_valid(issue, draft) and conf >= 0.6:
            validated.append(issue)
        else:
            weak.append(issue)
    evidence_coverage = len(validated) / max(1, len(all_must_fix))
    over_correction_risk = bool(len(all_must_fix) >= 3 and evidence_coverage < 0.34)
    avg_confidence = sum(confidences) / max(1, len(confidences))
    min_score = min(scores) if scores else 0.0
    gate_decision = "rewrite"
    progression_blockers = False
    for payload in payload_list:
        if (payload.get("transition_conflict") or []) or (payload.get("no_new_delta") or []):
            progression_blockers = True
            break
    if len(all_must_fix) == 0 and avg_confidence >= 0.72 and min_score >= 0.68:
        gate_decision = "accept_with_minor_polish"
    elif len(validated) == 0 and evidence_coverage < 0.25 and avg_confidence >= 0.8:
        gate_decision = "accept_with_minor_polish"
    if progression_blockers:
        gate_decision = "rewrite"
    return {
        "must_fix_total": len(all_must_fix),
        "must_fix_validated": len(validated),
        "must_fix_weak": len(weak),
        "evidence_coverage": round(evidence_coverage, 4),
        "avg_confidence": round(avg_confidence, 4),
        "min_score": round(min_score, 4),
        "over_correction_risk": over_correction_risk,
        "progression_blockers": progression_blockers,
        "decision": gate_decision,
        "validated_issues": validated[:4],
        "weak_issues": weak[:4],
    }
