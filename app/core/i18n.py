"""Multilingual support helpers for 9 languages and quality evaluation."""
from __future__ import annotations

import re
from typing import Tuple

LANGUAGES = {
    "zh": "中文",
    "en": "English",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "pt": "Português",
    "ja": "日本語",
    "ko": "한국어",
    "ar": "العربية",
}

_SCRIPT_HINTS = {
    "zh": r"[\u4e00-\u9fff]",
    "ja": r"[\u3040-\u30ff]",
    "ko": r"[\uac00-\ud7af]",
    "ar": r"[\u0600-\u06ff]",
}

_NATIVE_STYLE_PROFILE = {
    "zh": "简洁有力，段落节奏分明，避免翻译腔与机械连接词堆砌。",
    "en": "Natural native prose with idiomatic phrasing, varied sentence rhythm, and clear scene beats.",
    "es": "Estilo natural y fluido, expresiones idiomáticas, ritmo narrativo variado y diálogos verosímiles.",
    "fr": "Style naturel et idiomatique, rythme élégant, transitions fluides et dialogues crédibles.",
    "de": "Natuerlicher idiomatischer Stil, praezise Formulierungen, klare Struktur und glaubwuerdige Dialoge.",
    "pt": "Estilo natural e idiomatico, ritmo equilibrado e dialogos autenticos.",
    "ja": "自然な日本語文体、助詞の運用を正確にし、会話は口語として違和感なく表現する。",
    "ko": "자연스러운 한국어 문체, 어미/조사 사용의 일관성, 대화체의 현실감 유지.",
    "ar": "اسلوب عربي فصيح طبيعي، تراكيب سليمة، وتدفق سردي متوازن بدون ترجمة حرفية.",
}


def _normalize_lang_code(code: str) -> str:
    """Normalize language code to project canonical format."""
    if not code:
        return "en"
    c = code.strip().lower().replace("_", "-")
    alias = {
        "zh-cn": "zh",
        "zh-tw": "zh",
        "zh-hk": "zh",
        "en-us": "en",
        "en-gb": "en",
        "pt-br": "pt",
        "pt-pt": "pt",
        "es-es": "es",
        "fr-fr": "fr",
        "de-de": "de",
        "ja-jp": "ja",
        "ko-kr": "ko",
        "ar-sa": "ar",
    }
    if c in alias:
        return alias[c]
    if "-" in c:
        base = c.split("-", 1)[0]
        if base in LANGUAGES:
            return base
    return c


def get_language_name(code: str) -> str:
    """Get display name for language code."""
    normalized = _normalize_lang_code(code)
    return LANGUAGES.get(normalized, code)


def get_native_style_profile(code: str) -> str:
    """Get native-writer style profile for a target language."""
    normalized = _normalize_lang_code(code)
    return _NATIVE_STYLE_PROFILE.get(normalized, _NATIVE_STYLE_PROFILE["en"])


def evaluate_language_quality(text: str, lang_code: str) -> Tuple[float, str]:
    """
    Evaluate language quality and return (score, report).
    Score range: 0.0 - 1.0
    """
    if not text or len(text.strip()) < 50:
        return 0.2, "文本过短，无法进行有效语言质量评估。"

    lang_code = _normalize_lang_code(lang_code)
    score = 1.0
    report_parts: list[str] = []

    # 1) Script sanity check for non-latin languages
    hint = _SCRIPT_HINTS.get(lang_code)
    if hint:
        if not re.search(hint, text):
            score -= 0.35
            report_parts.append("目标语言文字脚本占比不足，疑似语言不匹配。")

    # 2) Detect language when langdetect is available
    try:
        from langdetect import detect_langs  # type: ignore

        candidates = detect_langs(text[:4000])
        top = candidates[0] if candidates else None
        detected = _normalize_lang_code(str(top.lang)) if top else "unknown"
        target_prob = 0.0
        for item in candidates:
            if _normalize_lang_code(str(item.lang)) == lang_code:
                target_prob = float(item.prob)
                break
        if detected != lang_code:
            score -= 0.18
        if target_prob < 0.6:
            score -= 0.2
        report_parts.append(
            f"自动检测语言 `{detected}`，目标语言概率 {target_prob:.2f}。"
        )
    except Exception:
        report_parts.append("未启用自动语言检测（langdetect 不可用），跳过该项。")

    # 3) Basic punctuation / sentence rhythm sanity
    sentence_like = re.split(r"[。！？!?\.]+", text)
    sentence_like = [s for s in sentence_like if s.strip()]
    if len(sentence_like) < 3:
        score -= 0.15
        report_parts.append("句子数量偏少，文本结构不充分。")
    avg_len = sum(len(s) for s in sentence_like) / max(len(sentence_like), 1)
    if avg_len > 180:
        score -= 0.1
        report_parts.append("句长偏高，阅读节奏可能不自然。")
    if len(sentence_like) >= 6:
        repeated_sentences = len(sentence_like) - len(set(s.strip() for s in sentence_like))
        if repeated_sentences >= 2:
            score -= 0.08
            report_parts.append("句子重复率偏高，表达可能机械。")

    # 4) Optional grammar check with language_tool_python
    try:
        import language_tool_python  # type: ignore

        tool_lang_map = {
            "zh": "zh-CN",
            "en": "en-US",
            "es": "es",
            "fr": "fr",
            "de": "de-DE",
            "pt": "pt-BR",
            "ja": "ja",
            "ko": "ko",
            "ar": "ar",
        }
        tool_lang = tool_lang_map.get(lang_code, "en-US")
        tool = None
        # Prefer local LanguageTool to avoid public API throttling.
        try:
            tool = language_tool_python.LanguageTool(tool_lang)
        except Exception:
            # Fallback to public API when local server is unavailable.
            tool = language_tool_python.LanguageToolPublicAPI(tool_lang)
        matches = tool.check(text[:4000])
        if matches:
            density = len(matches) / max(len(text[:4000]), 1) * 1000
            if density > 2.5:
                score -= 0.15
                report_parts.append(f"语法问题密度偏高（{len(matches)} 条）。")
    except Exception:
        report_parts.append("未启用语法检查（language_tool_python 不可用），跳过该项。")

    score = max(0.0, min(1.0, score))
    if not report_parts:
        report_parts.append("语言质量良好，符合目标语言表达习惯。")
    return score, " ".join(report_parts)


def score_language_quality(text: str, lang_code: str) -> float:
    """Backward-compatible score-only helper."""
    score, _ = evaluate_language_quality(text, lang_code)
    return score
