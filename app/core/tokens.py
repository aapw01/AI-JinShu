"""Token estimation helpers.

Use tiktoken when available for closer-to-real token counts, with a safe fallback.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_ENCODING = None
_ENCODING_READY = False


def _get_encoding():
    """Lazily load tiktoken encoding once."""
    global _ENCODING, _ENCODING_READY
    if _ENCODING_READY:
        return _ENCODING
    _ENCODING_READY = True
    try:
        import tiktoken  # type: ignore

        _ENCODING = tiktoken.get_encoding("cl100k_base")
    except Exception:
        _ENCODING = None
    return _ENCODING


def estimate_tokens(text: str) -> int:
    """Estimate token count for UI/progress/cost rough accounting."""
    if not text:
        return 1
    encoding = _get_encoding()
    if encoding is not None:
        try:
            return max(1, len(encoding.encode(text)))
        except Exception as exc:
            logger.debug("Token encoding failed, fallback to char heuristic: %s", exc)
    return max(1, len(text) // 4)
